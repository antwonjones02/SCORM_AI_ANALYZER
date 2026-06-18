"""Web app tests: upload → background job → report, via FastAPI TestClient."""

import time

import pytest

try:
    from fastapi.testclient import TestClient
    from scormweb.app import create_app
except ImportError:  # web extra not installed
    pytest.skip('fastapi not installed', allow_module_level=True)


@pytest.fixture()
def client(tmp_path):
    app = create_app(data_dir=tmp_path / 'jobs', max_workers=1)
    with TestClient(app) as c:
        yield c


def _wait_for_job(client, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f'/api/jobs/{job_id}').json()
        if job['status'] in ('done', 'error'):
            return job
        time.sleep(0.5)
    raise TimeoutError('job did not finish')


class TestEndpoints:
    def test_index_and_health(self, client):
        assert client.get('/healthz').json() == {'ok': True}
        page = client.get('/')
        assert page.status_code == 200
        assert 'SCORM AI Analyzer' in page.text

    def test_config(self, client):
        cfg = client.get('/api/config').json()
        assert 'llm_available' in cfg
        assert cfg['max_upload_mb'] > 0

    def test_rejects_non_zip(self, client):
        resp = client.post('/api/analyze',
                           files={'files': ('evil.exe', b'MZ...', 'application/o')})
        assert resp.status_code == 400

    def test_rejects_fake_zip(self, client):
        resp = client.post('/api/analyze',
                           files={'files': ('fake.zip', b'not a zip at all',
                                            'application/zip')})
        assert resp.status_code == 400

    def test_unknown_job_404(self, client):
        assert client.get('/api/jobs/nope').status_code == 404
        assert client.get('/jobs/nope/report').status_code == 404

    def test_combined_report_empty(self, client):
        assert client.get('/report').status_code == 404


class TestFullUploadFlow:
    def test_upload_analyze_report(self, client, basic_zip):
        resp = client.post(
            '/api/analyze',
            files={'files': ('course_basic_12.zip', basic_zip.read_bytes(),
                             'application/zip')},
            data={'play': 'false', 'llm': 'false'})
        assert resp.status_code == 200
        job_id = resp.json()['jobs'][0]['id']

        job = _wait_for_job(client, job_id)
        assert job['status'] == 'done'
        assert job['summary']['title'] == 'Workplace Fire Safety Essentials'
        assert job['summary']['score'] >= 70

        report = client.get(f'/jobs/{job_id}/report')
        assert report.status_code == 200
        assert 'Workplace Fire Safety Essentials' in report.text

        result = client.get(f'/api/jobs/{job_id}/result.json')
        assert result.status_code == 200
        assert result.json()['package_type'] == 'scorm'

        combined = client.get('/report')
        assert combined.status_code == 200
        assert 'Workplace Fire Safety Essentials' in combined.text

    def test_jobs_listing(self, client, tincan_zip):
        client.post('/api/analyze',
                    files={'files': ('course_tincan.zip', tincan_zip.read_bytes(),
                                     'application/zip')},
                    data={'play': 'false'})
        jobs = client.get('/api/jobs').json()['jobs']
        assert len(jobs) == 1
        assert jobs[0]['filename'] == 'course_tincan.zip'
