"""Background job manager for web uploads.

Single-instance design: jobs run in a thread pool inside the web process and
results persist to disk (one directory per job), so a restart re-lists past
jobs from their meta.json files. For multi-instance deployments put a real
queue (Celery/RQ) behind the same Job interface."""

import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scormlib import analyze
from scormlib.report_html import write_html_report

SAFE_NAME_RE = re.compile(r'[^A-Za-z0-9._-]+')


def safe_filename(name):
    name = Path(name or 'package.zip').name
    return SAFE_NAME_RE.sub('_', name)[:120] or 'package.zip'


@dataclass
class Job:
    id: str
    filename: str
    status: str = 'queued'            # queued | running | done | error
    created_at: float = field(default_factory=time.time)
    finished_at: float = None
    options: dict = field(default_factory=dict)
    error: str = None
    summary: dict = field(default_factory=dict)

    def to_public(self):
        d = asdict(self)
        d['report_url'] = f'/jobs/{self.id}/report' if self.status == 'done' else None
        d['json_url'] = f'/api/jobs/{self.id}/result.json' if self.status == 'done' else None
        return d


class JobManager:
    def __init__(self, data_dir, max_workers=2):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs = {}
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=max_workers,
                                           thread_name_prefix='scormjob')
        self._load_existing()

    # ── persistence ──────────────────────────────────────────────────────
    def _job_dir(self, job_id):
        return self.data_dir / job_id

    def _save_meta(self, job):
        (self._job_dir(job.id) / 'meta.json').write_text(
            json.dumps(asdict(job), indent=2))

    def _load_existing(self):
        for meta in sorted(self.data_dir.glob('*/meta.json')):
            try:
                data = json.loads(meta.read_text())
                job = Job(**{k: v for k, v in data.items()
                             if k in Job.__dataclass_fields__})
                if job.status in ('queued', 'running'):
                    job.status = 'error'
                    job.error = 'Interrupted by server restart'
                self.jobs[job.id] = job
            except (json.JSONDecodeError, TypeError):
                continue

    # ── public API ───────────────────────────────────────────────────────
    def submit(self, file_bytes, filename, options):
        job = Job(id=uuid.uuid4().hex[:12], filename=safe_filename(filename),
                  options=options)
        job_dir = self._job_dir(job.id)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / job.filename).write_bytes(file_bytes)
        with self.lock:
            self.jobs[job.id] = job
        self._save_meta(job)
        self.executor.submit(self._run, job.id)
        return job

    def get(self, job_id):
        return self.jobs.get(job_id)

    def list(self):
        return sorted(self.jobs.values(), key=lambda j: -j.created_at)

    def result_path(self, job_id):
        return self._job_dir(job_id) / 'result.json'

    def report_path(self, job_id):
        return self._job_dir(job_id) / 'report.html'

    def done_results(self):
        results = []
        for job in self.list():
            if job.status != 'done':
                continue
            try:
                results.append(json.loads(self.result_path(job.id).read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        return results

    # ── worker ───────────────────────────────────────────────────────────
    def _run(self, job_id):
        job = self.jobs[job_id]
        job.status = 'running'
        self._save_meta(job)
        job_dir = self._job_dir(job_id)
        try:
            result = analyze(
                job_dir / job.filename,
                use_player=job.options.get('play', True),
                use_llm=job.options.get('llm', False),
                llm_provider=job.options.get('llm_provider', 'claude'),
                max_slides=int(job.options.get('max_slides', 40)),
                screenshots_dir=job_dir / 'screenshots',
            )
            self.result_path(job_id).write_text(
                json.dumps(result, indent=2, ensure_ascii=False))
            write_html_report([result], self.report_path(job_id),
                              report_title=f'SCORM Report — {job.filename}')

            stats = result.get('stats', {})
            runtime = (result.get('player_pipeline') or {}).get('runtime', {})
            job.summary = {
                'title': result.get('metadata', {}).get('title') or job.filename,
                'package_type': result.get('package_type'),
                'scorm_version': result.get('scorm_version'),
                'authoring_tool': result.get('authoring_tool', {}).get('name'),
                'score': stats.get('ai_readiness_score'),
                'tier': stats.get('ai_readiness_tier'),
                'word_count': stats.get('word_count'),
                'quiz_questions': stats.get('quiz_question_count'),
                'completion_status': runtime.get('completion_status'),
                'error': result.get('error'),
            }
            job.status = 'error' if result.get('error') else 'done'
            job.error = result.get('error')
        except Exception as e:
            job.status = 'error'
            job.error = str(e)
        finally:
            job.finished_at = time.time()
            self._save_meta(job)
