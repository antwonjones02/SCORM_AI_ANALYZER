#!/usr/bin/env python3
"""
SCORM Analyzer — by Claw 🦊
Parses SCORM 1.2 and SCORM 2004 packages and extracts structured metadata.
Outputs clean JSON ready for LMS audits, AI readiness assessments, or A2A marketplaces.

Usage:
    python scorm_analyzer.py <path_to_scorm.zip> [--llm] [--video] [--llm-provider deepseek|claude] [--output output.json]

Options:
    --llm               Use AI to infer skills, generate summary
    --video             Extract + transcribe video (MP4) using Whisper, then re-score with transcript
    --llm-provider      'claude' (default) or 'deepseek' (uses DEEPSEEK_API_KEY)
    --output            Save JSON to file (default: print to stdout)

Pipeline (--video):
    1. Parser extracts metadata, scores course
    2. If MP4s found → ffmpeg extracts audio → Whisper transcribes
    3. Transcript fed to Claude for enriched re-score
    4. Before/after scores included in output
"""

import zipfile
import json
import sys
import os
import argparse
from xml.etree import ElementTree as ET
from pathlib import Path
import tempfile
import shutil

# SCORM XML Namespaces
NAMESPACES = {
    # SCORM 1.2
    'imscp_12':     'http://www.imsproject.org/xsd/imscp_rootv1p1p2',
    'adlcp_12':     'http://www.adlnet.org/xsd/adlcp_rootv1p2',
    'imsmd_12':     'http://www.imsglobal.org/xsd/imsmd_rootv1p2p1',
    # SCORM 2004
    'imscp_2004':   'http://www.imsglobal.org/xsd/imscp_v1p1',
    'adlcp_2004':   'http://www.adlnet.org/xsd/adlcp_v1p3',
    'imsmd_2004':   'http://ltsc.ieee.org/xsd/LOM',
    # Generic
    'dc':           'http://purl.org/dc/elements/1.1/',
    'lom':          'http://ltsc.ieee.org/xsd/LOM',
}

def detect_scorm_version(root):
    """Detect SCORM version from manifest namespace."""
    ns = root.tag
    if 'imscp_rootv1p1p2' in ns or 'imsproject' in ns:
        return '1.2'
    elif 'imscp_v1p1' in ns or 'imsglobal' in ns:
        return '2004'
    # Fallback: check schemaLocation or schema element
    for child in root:
        if 'schema' in child.tag.lower():
            text = child.text or ''
            if 'SCORM 1.2' in text:
                return '1.2'
            elif 'SCORM 2004' in text or '1.3' in text:
                return '2004'
    return 'unknown'

def strip_ns(tag):
    """Strip XML namespace from tag name."""
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag

def find_text(element, path, default=''):
    """Find text in element, stripping namespaces."""
    for child in element.iter():
        tag = strip_ns(child.tag)
        if tag == path and child.text:
            return child.text.strip()
    return default

def extract_metadata(root):
    """Extract top-level course metadata."""
    metadata = {
        'title': '',
        'description': '',
        'keywords': [],
        'version': '',
        'language': '',
        'duration': '',
        'copyright': '',
        'author': '',
    }

    # Walk through all elements looking for metadata fields
    for elem in root.iter():
        tag = strip_ns(elem.tag)
        text = (elem.text or '').strip()

        if tag == 'title' and not metadata['title'] and text:
            metadata['title'] = text
        elif tag in ('description', 'general') and not metadata['description']:
            # Try to find description nested inside
            for child in elem.iter():
                if strip_ns(child.tag) == 'string' and child.text:
                    metadata['description'] = child.text.strip()
                    break
                if strip_ns(child.tag) == 'description' and child.text:
                    metadata['description'] = child.text.strip()
                    break
        elif tag == 'keyword' and text:
            metadata['keywords'].append(text)
        elif tag == 'schemaversion' and text:
            metadata['version'] = text
        elif tag == 'language' and text and not metadata['language']:
            metadata['language'] = text
        elif tag in ('duration', 'typicalduration') and text:
            metadata['duration'] = text
        elif tag in ('copyrightandotherrestrictions', 'rights') and text:
            metadata['copyright'] = text
        elif tag in ('contribute', 'author') and not metadata['author']:
            for child in elem.iter():
                if strip_ns(child.tag) == 'entity' and child.text:
                    metadata['author'] = child.text.strip()
                    break

    return metadata

def extract_organizations(root):
    """Extract course structure from organizations element."""
    orgs = []

    for orgs_elem in root.iter():
        if strip_ns(orgs_elem.tag) == 'organizations':
            for org in orgs_elem:
                if strip_ns(org.tag) != 'organization':
                    continue
                org_data = {
                    'id': org.get('identifier', ''),
                    'title': '',
                    'items': []
                }
                for child in org:
                    tag = strip_ns(child.tag)
                    if tag == 'title':
                        org_data['title'] = (child.text or '').strip()
                    elif tag == 'item':
                        org_data['items'].append(parse_item(child))
                orgs.append(org_data)

    return orgs

def parse_item(item_elem, depth=0):
    """Recursively parse an item element."""
    item = {
        'id': item_elem.get('identifier', ''),
        'resource_ref': item_elem.get('identifierref', ''),
        'title': '',
        'objectives': [],
        'time_limit': '',
        'mastery_score': '',
        'children': []
    }

    for child in item_elem:
        tag = strip_ns(child.tag)
        if tag == 'title':
            item['title'] = (child.text or '').strip()
        elif tag in ('objectives', 'sequencingobjectives'):
            for obj in child.iter():
                obj_tag = strip_ns(obj.tag)
                if obj_tag in ('objective', 'primaryobjective'):
                    obj_id = obj.get('objectiveID') or obj.get('satisfiedbyprimaryobjective', '')
                    for desc in obj.iter():
                        if strip_ns(desc.tag) == 'description' and desc.text:
                            item['objectives'].append({
                                'id': obj_id,
                                'description': desc.text.strip()
                            })
        elif tag == 'timelimitaction':
            item['time_limit'] = (child.text or '').strip()
        elif tag == 'masteryscore':
            item['mastery_score'] = (child.text or '').strip()
        elif tag == 'item':
            item['children'].append(parse_item(child, depth + 1))

    return item

def extract_resources(root):
    """Extract resource inventory from resources element."""
    resources = []

    for res_elem in root.iter():
        if strip_ns(res_elem.tag) == 'resources':
            for res in res_elem:
                if strip_ns(res.tag) != 'resource':
                    continue
                resource = {
                    'id': res.get('identifier', ''),
                    'type': res.get('type', ''),
                    'href': res.get('href', ''),
                    'scorm_type': res.get('{http://www.adlnet.org/xsd/adlcp_rootv1p2}scormtype', '') or
                                  res.get('{http://www.adlnet.org/xsd/adlcp_v1p3}scormType', ''),
                    'files': []
                }
                for child in res:
                    if strip_ns(child.tag) == 'file':
                        href = child.get('href', '')
                        if href:
                            resource['files'].append(href)
                resources.append(resource)

    return resources

def extract_html_text(extract_dir, resources, max_chars=5000):
    """Extract readable text from HTML content files."""
    text_chunks = []
    total = 0

    for res in resources:
        if res.get('scorm_type', '').lower() == 'sco' and res.get('href'):
            html_path = Path(extract_dir) / res['href']
            if html_path.exists() and html_path.suffix.lower() in ('.html', '.htm'):
                try:
                    content = html_path.read_text(encoding='utf-8', errors='ignore')
                    # Simple HTML tag stripping
                    import re
                    text = re.sub(r'<[^>]+>', ' ', content)
                    text = re.sub(r'\s+', ' ', text).strip()
                    if text:
                        chunk = text[:1000]
                        text_chunks.append(chunk)
                        total += len(chunk)
                        if total >= max_chars:
                            break
                except Exception:
                    pass

    return '\n\n---\n\n'.join(text_chunks)

def flatten_items(items, depth=0):
    """Flatten item tree into list for counting/analysis."""
    flat = []
    for item in items:
        flat.append({'title': item['title'], 'depth': depth, 'id': item['id']})
        flat.extend(flatten_items(item.get('children', []), depth + 1))
    return flat

def extract_and_transcribe_videos(extract_dir, resources, whisper_model='base'):
    """
    Find MP4 files in extracted SCORM, transcribe with Whisper.
    Returns dict: {filename: transcript_text}
    """
    transcripts = {}
    mp4_files = []

    # Find all MP4s in extracted dir
    for root_dir, dirs, files in os.walk(extract_dir):
        for fname in files:
            if fname.lower().endswith('.mp4'):
                mp4_files.append(os.path.join(root_dir, fname))

    if not mp4_files:
        return transcripts

    try:
        import whisper
        import subprocess
        model = whisper.load_model(whisper_model)

        for mp4_path in mp4_files:
            fname = os.path.basename(mp4_path)
            # Extract audio with ffmpeg
            audio_path = mp4_path.replace('.mp4', '_audio.wav')
            try:
                result = subprocess.run(
                    ['ffmpeg', '-i', mp4_path, '-ac', '1', '-ar', '16000', '-vn', audio_path, '-y'],
                    capture_output=True, timeout=120
                )
                if result.returncode != 0:
                    transcripts[fname] = f"[ffmpeg error: {result.stderr.decode()[:200]}]"
                    continue

                # Transcribe
                wresult = model.transcribe(audio_path)
                transcripts[fname] = wresult.get('text', '').strip()

                # Cleanup audio file
                try:
                    os.remove(audio_path)
                except Exception:
                    pass

            except subprocess.TimeoutExpired:
                transcripts[fname] = "[Transcription timed out]"
            except Exception as e:
                transcripts[fname] = f"[Transcription error: {str(e)}]"

    except ImportError:
        transcripts['_error'] = 'openai-whisper not installed. Run: pip install openai-whisper'
    except Exception as e:
        transcripts['_error'] = f'Whisper init error: {str(e)}'

    return transcripts


def llm_enrich_with_transcript(metadata, organizations, transcript_text, api_key=None, provider='claude'):
    """Re-score course using full video transcript for enriched analysis."""
    try:
        course_title = metadata.get('title', 'Unknown Course')

        prompt = f"""You are an L&D analyst. Analyze this SCORM course based on its full video transcript.
Output ONLY valid JSON with these fields:
{{
  "ai_readiness_score": <0-100>,
  "ai_summary": "2-3 sentence summary of what this course teaches",
  "inferred_skills": ["skill1", "skill2", ...],
  "target_audience": "who this course is designed for",
  "difficulty_level": "Beginner|Intermediate|Advanced",
  "estimated_duration_minutes": <number or null>,
  "learning_objectives": ["objective1", "objective2", ...],
  "content_quality_flags": ["flag1", ...],
  "ai_readiness_notes": "explanation of score and what would improve it",
  "transcript_quality": "assessment of transcript completeness and coverage"
}}

Course Title: {course_title}

FULL TRANSCRIPT:
{transcript_text[:6000]}"""

        if provider == 'deepseek':
            import urllib.request
            ds_key = api_key or os.environ.get('DEEPSEEK_API_KEY')
            if not ds_key:
                return {"error": "DEEPSEEK_API_KEY not set"}
            payload = json.dumps({
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1500,
                "temperature": 0.3
            }).encode()
            req = urllib.request.Request(
                "https://api.deepseek.com/v1/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {ds_key}",
                    "Content-Type": "application/json"
                }
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                api_resp = json.loads(resp.read())
            raw = api_resp['choices'][0]['message']['content'].strip()
        else:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = response.content[0].text.strip()

        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1]
            raw = raw.rsplit('```', 1)[0]

        return json.loads(raw)

    except Exception as e:
        return {"error": f"LLM transcript enrichment failed: {str(e)}"}


def llm_enrich(metadata, organizations, content_text, api_key=None, provider='claude'):
    """Use Claude or DeepSeek to infer skills, generate summary from extracted content."""
    try:
        course_title = metadata.get('title', 'Unknown Course')
        description = metadata.get('description', '')

        modules = []
        for org in organizations:
            for item in flatten_items(org.get('items', [])):
                if item['title']:
                    modules.append(item['title'])

        prompt = f"""You are an L&D analyst. Analyze this SCORM course data and output a JSON object.

Course Title: {course_title}
Description: {description}
Modules/Items: {', '.join(modules[:30])}
Content Sample: {content_text[:2000]}

Output ONLY valid JSON with these fields:
{{
  "ai_summary": "2-3 sentence summary of what this course teaches",
  "inferred_skills": ["skill1", "skill2", ...],
  "target_audience": "who this course is designed for",
  "difficulty_level": "Beginner|Intermediate|Advanced",
  "estimated_duration_minutes": <number or null>,
  "content_quality_flags": ["flag1", ...],
  "ai_readiness_score": <0-100>,
  "ai_readiness_notes": "what's missing or needs improvement"
}}"""

        if provider == 'deepseek':
            import urllib.request
            ds_key = api_key or os.environ.get('DEEPSEEK_API_KEY')
            if not ds_key:
                return {"error": "DEEPSEEK_API_KEY not set"}
            payload = json.dumps({
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1024,
                "temperature": 0.3
            }).encode()
            req = urllib.request.Request(
                "https://api.deepseek.com/v1/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {ds_key}",
                    "Content-Type": "application/json"
                }
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                api_resp = json.loads(resp.read())
            raw = api_resp['choices'][0]['message']['content'].strip()
        else:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = response.content[0].text.strip()

        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1]
            raw = raw.rsplit('```', 1)[0]

        return json.loads(raw)

    except Exception as e:
        return {"error": f"LLM enrichment failed: {str(e)}"}

def run_player_pipeline(zip_path, metadata, organizations, extract_dir, llm_provider='claude'):
    """
    Launch headless Chromium via Playwright, capture screenshots, analyze with Claude vision.
    Returns player_pipeline dict matching the enriched JSON format.
    """
    import base64
    import threading
    from pathlib import Path as PPath
    from http.server import HTTPServer, SimpleHTTPRequestHandler

    # Import Playwright
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"error": "playwright not installed. Run: pip install playwright && playwright install chromium"}

    # SCORM API shim (inline copy from scorm_player.py pattern)
    SCORM_SHIM = """
(function() {
  if (window.__SCORM_CAPTURED__) return;
  window.__SCORM_CAPTURED__ = true;
  var _data = {};
  var API = {
    LMSInitialize: function(s) { return 'true'; },
    LMSFinish: function(s) { return 'true'; },
    LMSGetValue: function(k) { return _data[k] || ''; },
    LMSSetValue: function(k, v) { _data[k] = v; return 'true'; },
    LMSCommit: function(s) { return 'true'; },
    LMSGetLastError: function() { return '0'; },
    LMSGetErrorString: function(e) { return 'No error'; },
    LMSGetDiagnostic: function(e) { return ''; }
  };
  var API_1484_11 = {
    Initialize: function(s) { return 'true'; },
    Terminate: function(s) { return 'true'; },
    GetValue: function(k) { return _data[k] || ''; },
    SetValue: function(k, v) { _data[k] = v; return 'true'; },
    Commit: function(s) { return 'true'; },
    GetLastError: function() { return '0'; },
    GetErrorString: function(e) { return 'No error'; },
    GetDiagnostic: function(e) { return ''; }
  };
  window.API = API;
  window.API_1484_11 = API_1484_11;
  try { if (window.parent !== window) { window.parent.API = API; window.parent.API_1484_11 = API_1484_11; } } catch(e) {}
  console.log('[SCORM SHIM] Loaded.');
})();
"""

    # ── Interaction type detection ────────────────────────────────────────────
    INTERACTION_JS = """
() => {
  var d = document;
  // quiz
  if (d.querySelector('input[type=radio], input[type=checkbox]') ||
      d.querySelector('button[class*="submit" i], button[class*="check" i], input[type=submit]') ||
      /submit|check answer/i.test(d.body && d.body.innerText || '')) {
    return 'quiz';
  }
  // drag-drop
  if (d.querySelector('[draggable=true], [class*="drag" i], [class*="drop" i], [class*="DragDrop" i]')) {
    return 'drag-drop';
  }
  // hotspot
  if (d.querySelector('map area, [class*="hotspot" i], [class*="clickable-area" i]')) {
    return 'hotspot';
  }
  // video
  if (d.querySelector('video')) {
    return 'video';
  }
  // scenario — branching buttons with 2+ choices OR character images
  var btns = d.querySelectorAll('button:not([class*="next" i]):not([class*="prev" i]):not([class*="submit" i])');
  if (btns.length >= 2 && d.querySelector('img')) {
    return 'scenario';
  }
  // scroll (Rise) — no nav buttons, scrollable container
  var hasNext = d.querySelector('button[class*="next" i], [aria-label*="next" i], .NextButton');
  if (!hasNext) {
    return 'scroll';
  }
  return 'standard';
}
"""

    DOM_FINGERPRINT_JS = """
() => {
  return (document.body && document.body.innerHTML.length) || 0;
}
"""

    IS_QUIZ_SLIDE_JS = """
() => {
  var d = document;
  return !!(d.querySelector('input[type=radio], input[type=checkbox]') ||
            d.querySelector('button[class*="submit" i], button[class*="check" i]'));
}
"""

    # ── Rise detection ────────────────────────────────────────────────────────
    course_type = "unknown"
    # Check organizations data for Rise identifier
    org_str = json.dumps(organizations) if organizations else ""
    if "articulate_rise" in org_str.lower() or "rise" in org_str.lower():
        course_type = "rise"
    # Check file structure
    if course_type == "unknown":
        for root_dir, dirs, files in os.walk(extract_dir):
            for fname in files:
                if fname == "rise.js" or "rise" in root_dir.lower():
                    course_type = "rise"
                    break
            if course_type == "rise":
                break
        # Check scormcontent folder (Rise typically uses this)
        if os.path.isdir(os.path.join(extract_dir, "scormcontent")):
            course_type = "rise"

    # Screenshots output dir
    course_name = Path(zip_path).stem
    safe_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in course_name)
    screenshots_dir = Path('/home/ubuntu/.openclaw/workspace/output/screenshots') / safe_name
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    # Find entry point
    manifest_path = None
    for root_dir, dirs, files in os.walk(extract_dir):
        for fname in files:
            if fname.lower() == 'imsmanifest.xml':
                manifest_path = os.path.join(root_dir, fname)
                break
        if manifest_path:
            break

    entry_href = None
    if manifest_path:
        try:
            tree = ET.parse(manifest_path)
            root = tree.getroot()
            for elem in root.iter():
                tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                if tag == 'resource':
                    href = elem.get('href', '')
                    if href and href.lower().endswith(('.html', '.htm')):
                        if Path(extract_dir, href).exists():
                            entry_href = href
                            break
        except Exception:
            pass

    if not entry_href:
        for candidate in ['index_lms_html5.html', 'index_lms.html', 'story_html5.html', 'story.html', 'index.html']:
            if Path(extract_dir, candidate).exists():
                entry_href = candidate
                break

    if not entry_href:
        return {"error": "No HTML entry point found in SCORM package"}

    # Start local HTTP server
    class SilentHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass

    orig_dir = os.getcwd()
    os.chdir(extract_dir)
    port = 18765
    try:
        server = HTTPServer(('localhost', port), SilentHandler)
    except OSError:
        port = 18766
        server = HTTPServer(('localhost', port), SilentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    MAX_SCREENSHOTS = 15
    slides_data = []   # list of {index, screenshot_path, interaction_type, stalled}
    stall_count = 0

    def detect_interaction(page):
        """Detect interaction type from current DOM."""
        try:
            for frame in page.frames:
                try:
                    result = frame.evaluate(INTERACTION_JS)
                    if result and result != 'standard':
                        return result
                except Exception:
                    pass
            return page.evaluate(INTERACTION_JS)
        except Exception:
            return 'standard'

    def is_quiz_slide(page):
        """Check if current slide looks like a quiz."""
        try:
            for frame in page.frames:
                try:
                    result = frame.evaluate(IS_QUIZ_SLIDE_JS)
                    if result:
                        return True
                except Exception:
                    pass
            return page.evaluate(IS_QUIZ_SLIDE_JS)
        except Exception:
            return False

    def dom_fingerprint(page):
        """Get a fingerprint to detect DOM changes (stall detection)."""
        try:
            for frame in page.frames:
                try:
                    val = frame.evaluate(DOM_FINGERPRINT_JS)
                    if val and val > 100:
                        return val
                except Exception:
                    pass
            return page.evaluate(DOM_FINGERPRINT_JS)
        except Exception:
            return 0

    def _inject_shim_to_all_frames(page, shim):
        """Inject SCORM shim into all current frames."""
        for frame in page.frames:
            try:
                frame.evaluate(shim)
            except Exception:
                pass

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 720})
            context.add_init_script(SCORM_SHIM)  # applies to ALL frames including iframes
            page = context.new_page()

            # Re-inject shim whenever a new frame is attached
            page.on("frameattached", lambda frame: frame.evaluate(SCORM_SHIM) if True else None)

            url = f"http://localhost:{port}/{entry_href}"
            try:
                page.goto(url, timeout=20000, wait_until='domcontentloaded')
            except Exception:
                pass

            import time
            time.sleep(2)

            _inject_shim_to_all_frames(page, SCORM_SHIM)

            # ── Rise: scroll-based capture ────────────────────────────────────
            if course_type == "rise":
                scroll_y = 0
                scroll_increment = 800
                for shot_num in range(1, MAX_SCREENSHOTS + 1):
                    try:
                        shot_path = screenshots_dir / f"slide_{shot_num:03d}.png"
                        page.screenshot(path=str(shot_path))
                        interaction = detect_interaction(page)
                        slides_data.append({
                            "index": shot_num,
                            "screenshot_path": str(shot_path),
                            "interaction_type": interaction,
                            "stalled": False,
                        })
                        scroll_y += scroll_increment
                        page.evaluate(f"window.scrollTo(0, {scroll_y})")
                        time.sleep(0.8)
                    except Exception:
                        break

            else:
                # ── Storyline / standard: click-based capture ─────────────────
                # Phase 1: discover total slide count by navigating through all slides
                # We do a quick pass (up to 50 clicks) to count slides, recording quiz slides
                discovered_slides = []   # indices of quiz slides found during discovery
                slide_count = 0
                MAX_DISCOVERY = 50

                # Take first screenshot to initialize
                time.sleep(1.5)
                prev_fp = dom_fingerprint(page)

                # Navigate and count
                for i in range(MAX_DISCOVERY):
                    slide_count += 1
                    is_quiz = is_quiz_slide(page)
                    discovered_slides.append({"slide_index": slide_count, "is_quiz": is_quiz})

                    # Try to advance
                    advanced = False
                    for selector in [
                        'button:has-text("Next")', 'button:has-text("Continue")', 'button:has-text("Start")', 'button:has-text("Begin")', 'button:has-text("Launch")', 'a:has-text("Start")', '[class*="start" i]', '[class*="begin" i]',
                        'button:has-text("next")', 'a:has-text("Next")',
                        '[aria-label*="next" i]', '[class*="next" i]',
                        '.btn-next', '#next', '.NextButton'
                    ]:
                        try:
                            btn = page.query_selector(selector)
                            if btn and btn.is_visible():
                                btn.click()
                                advanced = True
                                break
                        except Exception:
                            pass
                    if not advanced:
                        try:
                            page.keyboard.press('ArrowRight')
                        except Exception:
                            pass

                    # Check stall
                    time.sleep(0.5)
                    new_fp = dom_fingerprint(page)
                    if new_fp == prev_fp:
                        # Try interaction-unlock strategies before giving up
                        _unlocked = False
                        # Strategy 1: JS force drag-drop
                        try:
                            s1_js = """() => {
  try {
    var draggables = document.querySelectorAll('[draggable="true"], .drag-item, .draggable, [class*="drag"]');
    var targets = document.querySelectorAll('.drop-target, .drop-zone, [class*="drop"], [class*="target"]');
    draggables.forEach(function(drag, i) {
      var target = targets[i] || targets[targets.length - 1];
      if (target) {
        var rect = target.getBoundingClientRect();
        ['dragstart','dragover','drop','dragend'].forEach(function(evtName) {
          var evt = new DragEvent(evtName, {bubbles:true, cancelable:true,
            clientX: rect.left + rect.width/2, clientY: rect.top + rect.height/2});
          (evtName === 'dragstart' || evtName === 'dragend' ? drag : target).dispatchEvent(evt);
        });
      }
    });
    document.querySelectorAll('[class*="submit" i], [class*="check" i], [class*="done" i]').forEach(function(b){b.click();});
    return 'ok';
  } catch(e) { return 'err'; }
}"""
                            for frame in page.frames:
                                try: frame.evaluate(s1_js)
                                except Exception: pass
                            page.evaluate(s1_js)
                        except Exception:
                            pass
                        # Strategy 2: Playwright drag simulation
                        try:
                            drag_els = page.query_selector_all('[draggable="true"], .drag-item, [class*="drag-item"]')
                            drop_els = page.query_selector_all('.drop-target, .drop-zone, [class*="drop-target"], [class*="drop-zone"]')
                            for di, del_ in enumerate(drag_els):
                                tgt = drop_els[di] if di < len(drop_els) else (drop_els[-1] if drop_els else None)
                                if tgt:
                                    try:
                                        db = del_.bounding_box(); tb = tgt.bounding_box()
                                        if db and tb:
                                            page.mouse.move(db['x']+db['width']/2, db['y']+db['height']/2)
                                            page.mouse.down()
                                            time.sleep(0.15)
                                            page.mouse.move(tb['x']+tb['width']/2, tb['y']+tb['height']/2)
                                            time.sleep(0.15)
                                            page.mouse.up()
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                        # Strategy 3: click all interactive elements
                        try:
                            for el in page.query_selector_all('button, [role="button"], [class*="option"], [class*="choice"]'):
                                try:
                                    lbl = (el.get_attribute('aria-label') or el.inner_text() or '').lower()
                                    if any(k in lbl for k in ('next','continue','finish','close')):
                                        continue
                                    if el.is_visible():
                                        el.click()
                                        time.sleep(0.05)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        # Strategy 4: SCORM API force complete
                        try:
                            scorm_js = """() => {
  [window.API, window.parent && window.parent.API].filter(Boolean).forEach(function(a){
    try{a.LMSSetValue('cmi.core.lesson_status','completed');}catch(e){}
    try{a.LMSSetValue('cmi.core.score.raw','100');}catch(e){}
    try{a.LMSCommit('');}catch(e){}
  });
  [window.API_1484_11, window.parent && window.parent.API_1484_11].filter(Boolean).forEach(function(a){
    try{a.SetValue('cmi.completion_status','completed');}catch(e){}
    try{a.SetValue('cmi.score.raw','100');}catch(e){}
    try{a.Commit('');}catch(e){}
  });
}"""
                            for frame in page.frames:
                                try: frame.evaluate(scorm_js)
                                except Exception: pass
                            page.evaluate(scorm_js)
                        except Exception:
                            pass
                        # Re-try clicking Next after all strategies
                        for selector in ['button:has-text("Next")', 'button:has-text("Continue")', 'button:has-text("Start")', 'button:has-text("Begin")', 'button:has-text("Launch")', 'a:has-text("Start")', '[class*="start" i]', '[class*="begin" i]',
                                         '[aria-label*="next" i]', '.NextButton', '#next']:
                            try:
                                btn = page.query_selector(selector)
                                if btn and btn.is_visible():
                                    btn.click()
                                    break
                            except Exception:
                                pass
                        time.sleep(1.0)
                        retry_fp = dom_fingerprint(page)
                        if retry_fp != prev_fp:
                            prev_fp = retry_fp
                            _unlocked = True
                        if not _unlocked:
                            # No DOM change after all strategies — truly at end or stuck
                            break
                    prev_fp = new_fp

                total_slides = slide_count

                # ── Smart sampling strategy ───────────────────────────────────
                quiz_indices = {s["slide_index"] for s in discovered_slides if s["is_quiz"]}

                if total_slides <= 15:
                    sampling_strategy = "all"
                    capture_indices = set(range(1, total_slides + 1))
                elif total_slides <= 50:
                    sampling_strategy = "interval"
                    step = max(1, total_slides // MAX_SCREENSHOTS)
                    capture_indices = set(range(1, total_slides + 1, step))
                    # Trim to MAX_SCREENSHOTS
                    capture_indices = set(sorted(capture_indices)[:MAX_SCREENSHOTS])
                else:
                    sampling_strategy = "head-sample-tail"
                    head = set(range(1, 6))          # first 5
                    tail = set(range(total_slides - 4, total_slides + 1))  # last 5
                    mid_start = 6
                    mid_end = total_slides - 5
                    mid_count = 5
                    if mid_end > mid_start:
                        step = max(1, (mid_end - mid_start) // mid_count)
                        middle = set(range(mid_start, mid_end + 1, step))
                        middle = set(sorted(middle)[:mid_count])
                    else:
                        middle = set()
                    capture_indices = head | middle | tail

                # Always add quiz slides (up to MAX_SCREENSHOTS total)
                capture_indices |= quiz_indices
                capture_indices = set(sorted(capture_indices)[:MAX_SCREENSHOTS])

                # ── Second pass: reload and capture sampled slides ─────────────
                try:
                    page.goto(url, timeout=20000, wait_until='domcontentloaded')
                except Exception:
                    pass
                time.sleep(2)
                for frame in page.frames:
                    try:
                        frame.evaluate(SCORM_SHIM)
                    except Exception:
                        pass

                current_slide = 0
                prev_fp = dom_fingerprint(page)

                for nav_step in range(1, total_slides + 1):
                    current_slide = nav_step
                    stalled = False

                    if current_slide in capture_indices:
                        time.sleep(1.0)
                        interaction = detect_interaction(page)
                        shot_path = screenshots_dir / f"slide_{current_slide:03d}.png"
                        try:
                            page.screenshot(path=str(shot_path))
                            slides_data.append({
                                "index": current_slide,
                                "screenshot_path": str(shot_path),
                                "interaction_type": interaction,
                                "stalled": stalled,
                            })
                        except Exception:
                            pass

                    if current_slide >= total_slides:
                        break

                    # Advance to next slide
                    advanced = False
                    for selector in [
                        'button:has-text("Next")', 'button:has-text("Continue")', 'button:has-text("Start")', 'button:has-text("Begin")', 'button:has-text("Launch")', 'a:has-text("Start")', '[class*="start" i]', '[class*="begin" i]',
                        'button:has-text("next")', 'a:has-text("Next")',
                        '[aria-label*="next" i]', '[class*="next" i]',
                        '.btn-next', '#next', '.NextButton'
                    ]:
                        try:
                            btn = page.query_selector(selector)
                            if btn and btn.is_visible():
                                btn.click()
                                advanced = True
                                break
                        except Exception:
                            pass
                    if not advanced:
                        try:
                            page.keyboard.press('ArrowRight')
                        except Exception:
                            pass

                    # Stall detection: wait up to 3s for DOM change
                    stall_deadline = time.time() + 3.0
                    changed = False
                    while time.time() < stall_deadline:
                        time.sleep(0.3)
                        new_fp = dom_fingerprint(page)
                        if new_fp != prev_fp:
                            prev_fp = new_fp
                            changed = True
                            break

                    if not changed:
                        # --- Drag-drop / locked-slide interaction handler ---
                        current_interaction = detect_interaction(page)
                        interaction_attempts = []
                        resolved = False

                        def _check_advanced(old_fp):
                            time.sleep(1.0)
                            nfp = dom_fingerprint(page)
                            return nfp != old_fp, nfp

                        # Strategy 1: JS force-complete drag-drop
                        if not resolved:
                            try:
                                s1_js = """
() => {
  try {
    var draggables = document.querySelectorAll('[draggable="true"], .drag-item, .draggable, [class*="drag"]');
    var targets = document.querySelectorAll('.drop-target, .drop-zone, [class*="drop"], [class*="target"]');
    draggables.forEach(function(drag, i) {
      var target = targets[i] || targets[targets.length - 1];
      if (target) {
        var rect = target.getBoundingClientRect();
        ['dragstart','dragover','drop','dragend'].forEach(function(evtName) {
          var evt = new DragEvent(evtName, {bubbles:true, cancelable:true,
            clientX: rect.left + rect.width/2, clientY: rect.top + rect.height/2});
          (evtName === 'dragstart' || evtName === 'dragend' ? drag : target).dispatchEvent(evt);
        });
      }
    });
    // Storyline-specific: click all submit/check buttons
    document.querySelectorAll('[class*="submit" i], [class*="check" i], [class*="done" i]').forEach(function(b){b.click();});
    return draggables.length + ':' + targets.length;
  } catch(e) { return 'err:' + e.message; }
}
"""
                                for frame in page.frames:
                                    try:
                                        frame.evaluate(s1_js)
                                    except Exception:
                                        pass
                                page.evaluate(s1_js)
                                # Try clicking next again
                                for selector in ['button:has-text("Next")', 'button:has-text("Continue")', 'button:has-text("Start")', 'button:has-text("Begin")', 'button:has-text("Launch")', 'a:has-text("Start")', '[class*="start" i]', '[class*="begin" i]',
                                                 '[aria-label*="next" i]', '.NextButton', '#next']:
                                    try:
                                        btn = page.query_selector(selector)
                                        if btn and btn.is_visible():
                                            btn.click()
                                            break
                                    except Exception:
                                        pass
                                adv, new_fp = _check_advanced(prev_fp)
                                interaction_attempts.append("strategy_1_js_force")
                                if adv:
                                    prev_fp = new_fp
                                    changed = True
                                    resolved = True
                            except Exception as e:
                                interaction_attempts.append(f"strategy_1_failed:{e}")

                        # Strategy 2: Playwright drag simulation
                        if not resolved:
                            try:
                                drag_els = page.query_selector_all('[draggable="true"], .drag-item, .draggable, [class*="drag-item"]')
                                drop_els = page.query_selector_all('.drop-target, .drop-zone, [class*="drop-target"], [class*="drop-zone"]')
                                if drag_els and drop_els:
                                    for di, drag_el in enumerate(drag_els):
                                        target_el = drop_els[di] if di < len(drop_els) else drop_els[-1]
                                        try:
                                            drag_box = drag_el.bounding_box()
                                            drop_box = target_el.bounding_box()
                                            if drag_box and drop_box:
                                                page.mouse.move(drag_box['x'] + drag_box['width']/2, drag_box['y'] + drag_box['height']/2)
                                                page.mouse.down()
                                                time.sleep(0.2)
                                                page.mouse.move(drop_box['x'] + drop_box['width']/2, drop_box['y'] + drop_box['height']/2)
                                                time.sleep(0.2)
                                                page.mouse.up()
                                                time.sleep(0.3)
                                        except Exception:
                                            pass
                                # Try clicking next again
                                for selector in ['button:has-text("Next")', 'button:has-text("Continue")', 'button:has-text("Start")', 'button:has-text("Begin")', 'button:has-text("Launch")', 'a:has-text("Start")', '[class*="start" i]', '[class*="begin" i]',
                                                 '[aria-label*="next" i]', '.NextButton', '#next']:
                                    try:
                                        btn = page.query_selector(selector)
                                        if btn and btn.is_visible():
                                            btn.click()
                                            break
                                    except Exception:
                                        pass
                                adv, new_fp = _check_advanced(prev_fp)
                                interaction_attempts.append("strategy_2_playwright_drag")
                                if adv:
                                    prev_fp = new_fp
                                    changed = True
                                    resolved = True
                            except Exception as e:
                                interaction_attempts.append(f"strategy_2_failed:{e}")

                        # Strategy 3: Click all interactive elements
                        if not resolved:
                            try:
                                clickables = page.query_selector_all('button, [role="button"], a, input, [class*="hotspot"], [class*="clickable"], [class*="option"], [class*="choice"]')
                                next_keywords = {'next', 'continue', 'submit', 'check', 'done', 'finish'}
                                for el in clickables:
                                    try:
                                        label = (el.get_attribute('aria-label') or el.inner_text() or '').lower()
                                        if any(k in label for k in next_keywords):
                                            continue  # already tried
                                        if el.is_visible():
                                            el.click()
                                            time.sleep(0.1)
                                    except Exception:
                                        pass
                                # Try clicking next again
                                for selector in ['button:has-text("Next")', 'button:has-text("Continue")', 'button:has-text("Start")', 'button:has-text("Begin")', 'button:has-text("Launch")', 'a:has-text("Start")', '[class*="start" i]', '[class*="begin" i]',
                                                 '[aria-label*="next" i]', '.NextButton', '#next']:
                                    try:
                                        btn = page.query_selector(selector)
                                        if btn and btn.is_visible():
                                            btn.click()
                                            break
                                    except Exception:
                                        pass
                                adv, new_fp = _check_advanced(prev_fp)
                                interaction_attempts.append("strategy_3_click_all")
                                if adv:
                                    prev_fp = new_fp
                                    changed = True
                                    resolved = True
                            except Exception as e:
                                interaction_attempts.append(f"strategy_3_failed:{e}")

                        # Strategy 4: Force SCORM completion via API injection
                        if not resolved:
                            try:
                                scorm_complete_js = """
() => {
  try {
    var apis = [window.API, window.parent && window.parent.API].filter(Boolean);
    apis.forEach(function(api) {
      try { api.LMSSetValue('cmi.core.lesson_status', 'completed'); } catch(e){}
      try { api.LMSSetValue('cmi.core.score.raw', '100'); } catch(e){}
      try { api.LMSSetValue('cmi.core.score.min', '0'); } catch(e){}
      try { api.LMSSetValue('cmi.core.score.max', '100'); } catch(e){}
      try { api.LMSCommit(''); } catch(e){}
    });
    var apis2 = [window.API_1484_11, window.parent && window.parent.API_1484_11].filter(Boolean);
    apis2.forEach(function(api) {
      try { api.SetValue('cmi.completion_status', 'completed'); } catch(e){}
      try { api.SetValue('cmi.success_status', 'passed'); } catch(e){}
      try { api.SetValue('cmi.score.raw', '100'); } catch(e){}
      try { api.Commit(''); } catch(e){}
    });
    return 'scorm_completed';
  } catch(e) { return 'err:' + e.message; }
}
"""
                                for frame in page.frames:
                                    try:
                                        frame.evaluate(scorm_complete_js)
                                    except Exception:
                                        pass
                                page.evaluate(scorm_complete_js)
                                # Try clicking next again
                                for selector in ['button:has-text("Next")', 'button:has-text("Continue")', 'button:has-text("Start")', 'button:has-text("Begin")', 'button:has-text("Launch")', 'a:has-text("Start")', '[class*="start" i]', '[class*="begin" i]',
                                                 '[aria-label*="next" i]', '.NextButton', '#next',
                                                 'button:has-text("Finish")', 'button:has-text("Close")']:
                                    try:
                                        btn = page.query_selector(selector)
                                        if btn and btn.is_visible():
                                            btn.click()
                                            break
                                    except Exception:
                                        pass
                                adv, new_fp = _check_advanced(prev_fp)
                                interaction_attempts.append("strategy_4_scorm_complete")
                                if adv:
                                    prev_fp = new_fp
                                    changed = True
                                    resolved = True
                            except Exception as e:
                                interaction_attempts.append(f"strategy_4_failed:{e}")

                        # Update slide record with interaction attempt data
                        if slides_data and slides_data[-1]["index"] == current_slide:
                            slides_data[-1]["interaction_resolved"] = resolved
                            slides_data[-1]["interaction_attempts"] = interaction_attempts
                        else:
                            # Capture stall screenshot if not already done
                            shot_path = screenshots_dir / f"slide_{current_slide:03d}_stall.png"
                            try:
                                page.screenshot(path=str(shot_path))
                                slides_data.append({
                                    "index": current_slide,
                                    "screenshot_path": str(shot_path),
                                    "interaction_type": current_interaction,
                                    "stalled": not resolved,
                                    "interaction_resolved": resolved,
                                    "interaction_attempts": interaction_attempts,
                                })
                            except Exception:
                                pass

                        if not resolved:
                            stall_count += 1
                            if slides_data and slides_data[-1]["index"] == current_slide:
                                slides_data[-1]["stalled"] = True
                            break  # Can't advance further, give up on this slide

            context.close()
            browser.close()

    except Exception as e:
        os.chdir(orig_dir)
        server.shutdown()
        return {"error": f"Playwright error: {str(e)}"}
    finally:
        os.chdir(orig_dir)
        server.shutdown()

    screenshots = [s["screenshot_path"] for s in slides_data]

    if not screenshots:
        return {"error": "No screenshots captured"}

    # Determine final sampling strategy for Rise
    if course_type == "rise":
        sampling_strategy = "all" if len(slides_data) <= 15 else "interval"
        total_slides = len(slides_data)

    # Analyze screenshots with Claude vision
    try:
        import anthropic
        client = anthropic.Anthropic()
    except Exception as e:
        return {
            "error": f"Anthropic client error: {str(e)}",
            "course_type": course_type,
            "sampling_strategy": sampling_strategy,
            "total_slides_detected": total_slides,
            "screenshots_captured": len(screenshots),
            "stall_count": stall_count,
            "slides": slides_data,
        }

    # Build multi-image prompt
    content = []
    for i, slide in enumerate(slides_data):
        try:
            img_bytes = Path(slide["screenshot_path"]).read_bytes()
            img_b64 = base64.standard_b64encode(img_bytes).decode()
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": img_b64}
            })
            content.append({"type": "text", "text": f"[Slide {slide['index']} | type: {slide['interaction_type']}]"})
        except Exception:
            pass

    course_title = metadata.get('title', 'Unknown Course')
    content.append({"type": "text", "text": f"""You are an L&D analyst reviewing screenshots of a SCORM e-learning course.
Course title: {course_title}

Analyze ALL the slides shown above and output ONLY valid JSON:
{{
  "ai_readiness_score": <0-100>,
  "ai_summary": "2-3 sentence summary of what this course teaches",
  "inferred_skills": ["skill1", "skill2", ...],
  "target_audience": "who this course is for",
  "difficulty_level": "Beginner|Intermediate|Advanced",
  "estimated_duration_minutes": <number or null>,
  "learning_objectives": ["objective1", "objective2", ...],
  "quiz_questions": ["question1", ...],
  "content_quality_flags": ["flag1", ...],
  "ai_readiness_notes": "explanation of score",
  "slide_summaries": ["slide 1: ...", "slide 2: ...", ...]
}}"""})

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": content}]
        )
        raw = response.content[0].text.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
        visual_analysis = json.loads(raw)
    except Exception as e:
        visual_analysis = {"error": f"Vision analysis failed: {str(e)}"}

    return {
        "course_type": course_type,
        "sampling_strategy": sampling_strategy,
        "total_slides_detected": total_slides,
        "screenshots_captured": len(screenshots),
        "stall_count": stall_count,
        "slides": slides_data,
        "screenshots": screenshots,  # keep for backward compat
        "visual_analysis": visual_analysis,
    }


def analyze_scorm(zip_path, use_llm=False, use_video=False, use_player=False, llm_provider='claude', whisper_model='base'):
    """Main analysis function. Returns structured dict."""
    result = {
        'source_file': str(zip_path),
        'scorm_version': 'unknown',
        'metadata': {},
        'organizations': [],
        'resources': [],
        'stats': {},
        'llm_analysis': None,
        'player_pipeline': None,
        '_llm_provider': llm_provider
    }

    if not Path(zip_path).exists():
        result['error'] = f"File not found: {zip_path}"
        return result

    extract_dir = tempfile.mkdtemp(prefix='scorm_')

    try:
        # Extract ZIP
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(extract_dir)

        # Find imsmanifest.xml
        manifest_path = None
        for root_dir, dirs, files in os.walk(extract_dir):
            for fname in files:
                if fname.lower() == 'imsmanifest.xml':
                    manifest_path = os.path.join(root_dir, fname)
                    break
            if manifest_path:
                break

        if not manifest_path:
            result['error'] = 'imsmanifest.xml not found in package'
            return result

        # Parse XML
        tree = ET.parse(manifest_path)
        root = tree.getroot()

        # Detect version
        result['scorm_version'] = detect_scorm_version(root)

        # Extract components
        result['metadata'] = extract_metadata(root)
        result['organizations'] = extract_organizations(root)
        result['resources'] = extract_resources(root)

        # Stats
        all_items = []
        for org in result['organizations']:
            all_items.extend(flatten_items(org.get('items', [])))

        sco_count = sum(1 for r in result['resources'] if 'sco' in r.get('scorm_type', '').lower())
        asset_count = sum(1 for r in result['resources'] if 'asset' in r.get('scorm_type', '').lower())

        result['stats'] = {
            'total_modules': len(all_items),
            'top_level_items': sum(len(org.get('items', [])) for org in result['organizations']),
            'total_resources': len(result['resources']),
            'sco_count': sco_count,
            'asset_count': asset_count,
            'has_description': bool(result['metadata'].get('description')),
            'has_keywords': bool(result['metadata'].get('keywords')),
            'has_objectives': any(
                item.get('objectives')
                for item in all_items
                if isinstance(item, dict)
            ),
            'keyword_count': len(result['metadata'].get('keywords', [])),
        }

        # LLM enrichment
        if use_llm or use_video:
            api_key = use_llm if isinstance(use_llm, str) else None
            provider = result.get('_llm_provider', 'claude')
            content_text = extract_html_text(extract_dir, result['resources'])
            result['llm_analysis'] = llm_enrich(
                result['metadata'],
                result['organizations'],
                content_text,
                api_key=api_key,
                provider=provider
            )

        # Player pipeline (Playwright + Claude vision)
        if use_player:
            score_before = None
            if result.get('llm_analysis') and 'ai_readiness_score' in result['llm_analysis']:
                score_before = result['llm_analysis']['ai_readiness_score']

            player_result = run_player_pipeline(
                zip_path, result['metadata'], result['organizations'], extract_dir,
                llm_provider=llm_provider
            )

            score_after = None
            if player_result.get('visual_analysis') and 'ai_readiness_score' in player_result.get('visual_analysis', {}):
                score_after = player_result['visual_analysis']['ai_readiness_score']

                # If player analysis succeeded, use it as the primary LLM analysis
                if not result.get('llm_analysis') or result['llm_analysis'].get('error'):
                    result['llm_analysis'] = player_result['visual_analysis']

            result['player_pipeline'] = {
                'course_type': player_result.get('course_type', 'unknown'),
                'sampling_strategy': player_result.get('sampling_strategy'),
                'total_slides_detected': player_result.get('total_slides_detected'),
                'screenshots_captured': player_result.get('screenshots_captured'),
                'stall_count': player_result.get('stall_count', 0),
                'slides': player_result.get('slides', []),
                'screenshots': player_result.get('screenshots', []),  # backward compat
                'visual_analysis': player_result.get('visual_analysis'),
                'score_before': score_before,
                'score_after': score_after,
                'error': player_result.get('error'),
            }

        # Video transcription pipeline
        if use_video:
            result['video_pipeline'] = {
                'transcripts': {},
                'enriched_analysis': None,
                'score_before': None,
                'score_after': None
            }

            # Capture baseline score
            if result.get('llm_analysis') and 'ai_readiness_score' in result['llm_analysis']:
                result['video_pipeline']['score_before'] = result['llm_analysis']['ai_readiness_score']

            # Extract and transcribe
            transcripts = extract_and_transcribe_videos(extract_dir, result['resources'], whisper_model=whisper_model)
            result['video_pipeline']['transcripts'] = transcripts

            # Re-score if we got transcripts
            combined_transcript = '\n\n'.join(
                f"[{fname}]\n{text}"
                for fname, text in transcripts.items()
                if not fname.startswith('_') and text and not text.startswith('[')
            )

            if combined_transcript:
                api_key = use_llm if isinstance(use_llm, str) else None
                enriched = llm_enrich_with_transcript(
                    result['metadata'],
                    result['organizations'],
                    combined_transcript,
                    api_key=api_key,
                    provider=provider
                )
                result['video_pipeline']['enriched_analysis'] = enriched
                if enriched and 'ai_readiness_score' in enriched:
                    result['video_pipeline']['score_after'] = enriched['ai_readiness_score']
            elif '_error' in transcripts:
                result['video_pipeline']['error'] = transcripts['_error']
            else:
                result['video_pipeline']['note'] = 'No MP4 files found in this SCORM package — video pipeline skipped'

    except zipfile.BadZipFile:
        result['error'] = 'Not a valid ZIP/SCORM file'
    except ET.ParseError as e:
        result['error'] = f'XML parse error: {str(e)}'
    except Exception as e:
        result['error'] = f'Unexpected error: {str(e)}'
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)

    return result

def main():
    parser = argparse.ArgumentParser(description='SCORM Analyzer — extract structured data from SCORM packages')
    parser.add_argument('scorm_file', help='Path to SCORM .zip file')
    parser.add_argument('--llm', nargs='?', const=True, help='Use AI to infer skills and generate summary')
    parser.add_argument('--video', action='store_true', help='Extract + transcribe video (MP4) with Whisper, then re-score with transcript')
    parser.add_argument('--player', action='store_true', help='Launch headless Chromium, capture screenshots, analyze with Claude vision')
    parser.add_argument('--whisper-model', default='base', choices=['tiny', 'base', 'small', 'medium', 'large'], help='Whisper model size (default: base)')
    parser.add_argument('--llm-provider', default='claude', choices=['claude', 'deepseek'], help='AI provider for --llm mode (default: claude)')
    parser.add_argument('--output', help='Output JSON file path (default: stdout)')
    args = parser.parse_args()

    result = analyze_scorm(
        args.scorm_file,
        use_llm=args.llm or args.video,  # --video implies --llm for baseline score
        use_video=args.video,
        use_player=args.player,
        llm_provider=args.llm_provider,
        whisper_model=args.whisper_model
    )

    output = json.dumps(result, indent=2)

    if args.output:
        Path(args.output).write_text(output)
        print(f"✅ Analysis saved to: {args.output}")
    else:
        print(output)

if __name__ == '__main__':
    main()
