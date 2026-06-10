"""Top-level orchestrator: extract → identify → parse → mine → score →
(optionally) play in browser, transcribe video, enrich with LLM.

The output dict is a superset of the legacy scorm_analyzer.py format, so all
existing report generators keep working."""

import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from . import content as content_mod
from . import llm as llm_mod
from . import manifest as manifest_mod
from . import package as package_mod
from . import scoring


def analyze(zip_path, use_llm=False, use_video=False, use_player=False,
            llm_provider='claude', whisper_model='base', api_key=None,
            screenshots_dir=None, max_slides=40, keep_extracted=None,
            verbose=False):
    """Analyze a SCORM/xAPI/cmi5/AICC package zip. Returns the full result dict."""
    zip_path = Path(zip_path)
    log = (lambda *a: print(*a, flush=True)) if verbose else (lambda *a: None)

    result = {
        'analyzer_version': '2.0',
        'analyzed_at': datetime.now(timezone.utc).isoformat(),
        'source_file': str(zip_path),
        'package_type': 'unknown',
        'scorm_version': 'unknown',
        'authoring_tool': {'name': 'unknown', 'confidence': 'none', 'evidence': ''},
        'metadata': {},
        'organizations': [],
        'resources': [],
        'files': {},
        'content': {},
        'stats': {},
        'llm_analysis': None,
        'player_pipeline': None,
        '_llm_provider': llm_provider,
    }

    if not zip_path.exists():
        result['error'] = f'File not found: {zip_path}'
        return result
    if not zipfile.is_zipfile(zip_path):
        result['error'] = 'Not a valid ZIP/SCORM file'
        return result

    result['file_size_bytes'] = zip_path.stat().st_size
    result['sha256'] = package_mod.file_sha256(zip_path)

    extract_dir = keep_extracted or tempfile.mkdtemp(prefix='scorm_')
    cleanup = keep_extracted is None
    try:
        log(f'📦 Extracting {zip_path.name} ...')
        package_mod.safe_extract(zip_path, extract_dir)

        # ── Identify the standard ────────────────────────────────────────
        ptype = package_mod.detect_package_type(extract_dir)
        result['package_type'] = ptype['type']
        result['authoring_tool'] = package_mod.detect_authoring_tool(extract_dir)
        log(f"   type={ptype['type']}  tool={result['authoring_tool']['name']}")

        metadata = {}
        organizations = []
        resources = []

        if ptype['type'] == 'scorm':
            try:
                parsed = manifest_mod.parse_manifest(ptype['descriptor'])
            except ET.ParseError as e:
                result['error'] = f'XML parse error in imsmanifest.xml: {e}'
                return result
            result['scorm_version'] = parsed['scorm_version']
            metadata = parsed['metadata']
            organizations = parsed['organizations']
            resources = parsed['resources']
        elif ptype['type'] == 'xapi':
            info = package_mod.parse_tincan(ptype['descriptor'])
            result['scorm_version'] = 'xAPI (Tin Can)'
            metadata = _descriptor_to_metadata(info)
            metadata['activity_id'] = info.get('activity_id', '')
        elif ptype['type'] == 'cmi5':
            info = package_mod.parse_cmi5(ptype['descriptor'])
            result['scorm_version'] = 'cmi5'
            metadata = _descriptor_to_metadata(info)
            metadata['identifier'] = info.get('course_id', '')
        elif ptype['type'] == 'aicc':
            info = package_mod.parse_aicc(ptype['descriptor'])
            result['scorm_version'] = 'AICC'
            metadata = _descriptor_to_metadata(info)
        else:
            result['error'] = ('No recognized descriptor found (imsmanifest.xml, '
                               'tincan.xml, cmi5.xml or *.crs)')
            return result

        result['metadata'] = metadata
        result['organizations'] = organizations
        result['resources'] = resources

        # ── File inventory ───────────────────────────────────────────────
        result['files'] = package_mod.build_inventory(extract_dir)
        result['files']['entry_point'] = package_mod.find_entry_point(
            extract_dir, resources, ptype)

        # ── Deterministic content mining ─────────────────────────────────
        log('🔍 Mining content (HTML, Storyline/Rise data, captions) ...')
        content = content_mod.extract_content(extract_dir)
        result['content'] = content

        # Backfill metadata gaps from mined content
        embedded = content.get('embedded_metadata', {})
        if not metadata.get('title') and (embedded.get('title') or embedded.get('page_title')):
            metadata['title'] = embedded.get('title') or embedded.get('page_title')
        if not metadata.get('description') and embedded.get('description'):
            metadata['description'] = embedded['description']

        # ── Stats + deterministic score ──────────────────────────────────
        all_items = []
        for org in organizations:
            all_items.extend(manifest_mod.flatten_items(org.get('items', [])))
        sco_count = sum(1 for r in resources
                        if 'sco' in (r.get('scorm_type') or '').lower())
        asset_count = sum(1 for r in resources
                          if 'asset' in (r.get('scorm_type') or '').lower())

        score, breakdown = scoring.readiness_score(
            metadata, organizations, content, result['files'])

        result['stats'] = {
            'total_modules': len(all_items),
            'top_level_items': sum(len(org.get('items', [])) for org in organizations),
            'total_resources': len(resources),
            'sco_count': sco_count,
            'asset_count': asset_count,
            'has_description': bool(metadata.get('description')),
            'has_keywords': bool(metadata.get('keywords')),
            'has_objectives': any(item.get('objectives') for item in all_items),
            'has_duration': bool(metadata.get('duration_minutes')),
            'keyword_count': len(metadata.get('keywords', [])),
            'word_count': content.get('word_count', 0),
            'quiz_question_count': len(content.get('quiz_questions', [])),
            'ai_readiness_score': score,
            'ai_readiness_tier': scoring.score_to_tier(score),
            'score_breakdown': breakdown,
        }
        log(f"📊 Baseline AI-readiness score: {score}/100 "
            f"({result['stats']['ai_readiness_tier']})")

        # ── LLM enrichment (optional) ────────────────────────────────────
        if use_llm:
            if llm_mod.llm_available(llm_provider) or api_key:
                log(f'🧠 LLM enrichment via {llm_provider} ...')
                result['llm_analysis'] = llm_mod.enrich(
                    metadata, organizations, content, provider=llm_provider,
                    api_key=api_key, baseline_score=score)
            else:
                result['llm_analysis'] = {
                    'error': f'No API key for provider "{llm_provider}" '
                             '(set ANTHROPIC_API_KEY or DEEPSEEK_API_KEY)'}

        # ── Player pipeline (optional) ───────────────────────────────────
        if use_player:
            result['player_pipeline'] = _run_player(
                zip_path, result, extract_dir, screenshots_dir, max_slides,
                llm_provider, api_key, log, verbose)

        # ── Video transcription pipeline (optional) ──────────────────────
        if use_video:
            result['video_pipeline'] = _run_video(
                result, extract_dir, whisper_model, llm_provider, api_key, log)

    except zipfile.BadZipFile:
        result['error'] = 'Not a valid ZIP/SCORM file'
    except Exception as e:
        result['error'] = f'Unexpected error: {e}'
    finally:
        if cleanup:
            shutil.rmtree(extract_dir, ignore_errors=True)

    return result


def _descriptor_to_metadata(info):
    return {
        'title': info.get('title', ''),
        'description': info.get('description', ''),
        'keywords': [],
        'version': '',
        'language': '',
        'duration': '',
        'duration_minutes': None,
        'copyright': '',
        'author': '',
        'launch': info.get('launch', ''),
    }


def _run_player(zip_path, result, extract_dir, screenshots_dir, max_slides,
                llm_provider, api_key, log, verbose=False):
    """Run the headless browser player + optional vision analysis."""
    from . import player as player_mod

    entry = result['files'].get('entry_point')
    if not entry:
        return {'error': 'No HTML entry point found in package'}

    if not screenshots_dir:
        safe = ''.join(c if c.isalnum() or c in '-_' else '_'
                       for c in Path(zip_path).stem)
        base = Path(os.environ.get('SCORM_OUTPUT_DIR', 'output'))
        screenshots_dir = base / 'screenshots' / safe

    log(f'🎮 Playing course in headless browser (entry: {entry}) ...')
    try:
        play = player_mod.play_package(
            extract_dir, entry, screenshots_dir=screenshots_dir,
            max_slides=max_slides, organizations=result['organizations'],
            verbose=verbose)
    except ImportError:
        return {'error': 'playwright not installed. '
                         'Run: pip install playwright && playwright install chromium'}

    score_before = result['stats'].get('ai_readiness_score')
    runtime = play.get('runtime', {})
    log(f"   {play.get('screenshots_captured', 0)} screenshots | "
        f"completion: {runtime.get('completion_status', '?')} | "
        f"score: {runtime.get('score_raw')}")

    pipeline = {
        'course_type': play.get('course_type'),
        'entry_point': entry,
        'sampling_strategy': 'sequential',
        'total_slides_detected': play.get('total_slides_detected'),
        'screenshots_captured': play.get('screenshots_captured'),
        'stall_count': play.get('stall_count', 0),
        'slides': play.get('slides', []),
        'screenshots': play.get('screenshots', []),
        'completed_naturally': play.get('completed_naturally'),
        'forced_completion': play.get('forced_completion'),
        'runtime': runtime,
        'score_before': score_before,
        'score_after': None,
        'visual_analysis': None,
        'error': play.get('error'),
    }

    # Vision analysis of screenshots (needs Anthropic key)
    if play.get('screenshots') and llm_mod.llm_available('claude'):
        log('👁  Vision-analyzing screenshots ...')
        visual = llm_mod.vision_analyze_slides(
            play.get('slides', []), result['metadata'].get('title', 'Unknown'),
            api_key=api_key)
        pipeline['visual_analysis'] = visual
        if isinstance(visual.get('ai_readiness_score'), (int, float)):
            pipeline['score_after'] = visual['ai_readiness_score']
            if not result.get('llm_analysis') or \
                    (result.get('llm_analysis') or {}).get('error'):
                result['llm_analysis'] = visual

    return pipeline


def _run_video(result, extract_dir, whisper_model, llm_provider, api_key, log):
    """Find MP4s, transcribe with Whisper, re-score with the transcript."""
    pipeline = {'transcripts': {}, 'enriched_analysis': None,
                'score_before': result['stats'].get('ai_readiness_score'),
                'score_after': None}

    mp4_files = []
    for root_dir, _dirs, files in os.walk(extract_dir):
        for fname in files:
            if fname.lower().endswith(('.mp4', '.webm', '.m4v')):
                mp4_files.append(os.path.join(root_dir, fname))
    if not mp4_files:
        pipeline['note'] = 'No video files found — video pipeline skipped'
        return pipeline

    try:
        import whisper
    except ImportError:
        pipeline['error'] = 'openai-whisper not installed. Run: pip install openai-whisper'
        return pipeline

    log(f'🎬 Transcribing {len(mp4_files)} video(s) with Whisper ({whisper_model}) ...')
    try:
        model = whisper.load_model(whisper_model)
    except Exception as e:
        pipeline['error'] = f'Whisper init error: {e}'
        return pipeline

    ffmpeg = _resolve_ffmpeg()
    transcripts = {}
    for mp4_path in mp4_files:
        fname = os.path.basename(mp4_path)
        audio_path = mp4_path + '_audio.wav'
        try:
            proc = subprocess.run(
                [ffmpeg, '-i', mp4_path, '-ac', '1', '-ar', '16000', '-vn',
                 audio_path, '-y'],
                capture_output=True, timeout=300)
            if proc.returncode != 0:
                transcripts[fname] = f'[ffmpeg error: {proc.stderr.decode()[:200]}]'
                continue
            wresult = model.transcribe(audio_path)
            transcripts[fname] = wresult.get('text', '').strip()
        except subprocess.TimeoutExpired:
            transcripts[fname] = '[Transcription timed out]'
        except FileNotFoundError:
            transcripts[fname] = '[ffmpeg not installed]'
        except Exception as e:
            transcripts[fname] = f'[Transcription error: {e}]'
        finally:
            try:
                os.remove(audio_path)
            except OSError:
                pass

    pipeline['transcripts'] = transcripts
    combined = '\n\n'.join(f'[{f}]\n{t}' for f, t in transcripts.items()
                           if t and not t.startswith('['))
    if combined and (llm_mod.llm_available(llm_provider) or api_key):
        log('🧠 Re-scoring with transcript ...')
        enriched = llm_mod.enrich(
            result['metadata'], result['organizations'], result['content'],
            provider=llm_provider, api_key=api_key,
            baseline_score=pipeline['score_before'], transcript=combined)
        pipeline['enriched_analysis'] = enriched
        if isinstance(enriched.get('ai_readiness_score'), (int, float)):
            pipeline['score_after'] = enriched['ai_readiness_score']
    return pipeline


def _resolve_ffmpeg():
    """Find ffmpeg: PATH, env override, or Playwright's bundled binary."""
    found = os.environ.get('SCORM_FFMPEG_PATH') or shutil.which('ffmpeg')
    if found:
        return found
    cache = Path(os.environ.get('PLAYWRIGHT_BROWSERS_PATH',
                                Path.home() / '.cache' / 'ms-playwright'))
    for candidate in sorted(cache.glob('ffmpeg-*/ffmpeg-linux'), reverse=True):
        return str(candidate)
    return 'ffmpeg'


def save_result(result, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return output_path
