#!/usr/bin/env python3
"""
SCORM Player Agent — by Claw 🦊
Renders a SCORM package in a real browser, navigates through it like a learner,
captures all LMS data, takes screenshots, and generates a full course report.

Usage:
    python3 scorm_player.py <path_to_scorm.zip> [--output report.json] [--screenshots]

Requirements:
    pip install playwright anthropic
    playwright install chromium
"""

import zipfile
import json
import os
import sys
import argparse
import shutil
import tempfile
import threading
import time
import base64
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
import anthropic

# ─────────────────────────────────────────────
# SCORM API Shim (injected into every page)
# Captures all LMS data calls transparently
# ─────────────────────────────────────────────
SCORM_API_SHIM = """
(function() {
  if (window.__SCORM_CAPTURED__) return;
  window.__SCORM_CAPTURED__ = true;

  var _data = {};
  var _interactions = {};
  var _log = [];
  var _sessionStart = Date.now();

  function logEntry(fn, key, value) {
    _log.push({ fn: fn, key: key, value: value, t: Date.now() - _sessionStart });
  }

  var API = {
    LMSInitialize: function(s) { logEntry('LMSInitialize', null, null); return 'true'; },
    LMSFinish: function(s) { logEntry('LMSFinish', null, null); return 'true'; },
    LMSGetValue: function(key) {
      var val = _data[key] || '';
      logEntry('LMSGetValue', key, val);
      return val;
    },
    LMSSetValue: function(key, value) {
      _data[key] = value;
      logEntry('LMSSetValue', key, value);
      // Track interactions separately
      if (key && key.indexOf('cmi.interactions') === 0) {
        var parts = key.split('.');
        if (parts.length >= 3) {
          var idx = parts[2];
          if (!_interactions[idx]) _interactions[idx] = {};
          var subkey = parts.slice(3).join('.');
          _interactions[idx][subkey] = value;
        }
      }
      return 'true';
    },
    LMSCommit: function(s) { logEntry('LMSCommit', null, null); return 'true'; },
    LMSGetLastError: function() { return '0'; },
    LMSGetErrorString: function(e) { return 'No error'; },
    LMSGetDiagnostic: function(e) { return ''; },
  };

  // SCORM 2004 API (same shim, different names)
  var API_1484_11 = {
    Initialize: function(s) { return 'true'; },
    Terminate: function(s) { return 'true'; },
    GetValue: function(key) { return _data[key] || ''; },
    SetValue: function(key, value) {
      _data[key] = value;
      logEntry('SetValue', key, value);
      return 'true';
    },
    Commit: function(s) { return 'true'; },
    GetLastError: function() { return '0'; },
    GetErrorString: function(e) { return 'No error'; },
    GetDiagnostic: function(e) { return ''; },
  };

  // Expose on window and all parent frames the course might look for
  window.API = API;
  window.API_1484_11 = API_1484_11;
  if (window.parent && window.parent !== window) {
    try { window.parent.API = API; } catch(e) {}
    try { window.parent.API_1484_11 = API_1484_11; } catch(e) {}
  }

  // Reporter: call window.__SCORM_REPORT__() to get captured data
  window.__SCORM_REPORT__ = function() {
    return JSON.stringify({
      data: _data,
      interactions: _interactions,
      log: _log,
      elapsedMs: Date.now() - _sessionStart
    });
  };

  console.log('[SCORM SHIM] Loaded. API ready.');
})();
"""

# ─────────────────────────────────────────────
# Local HTTP Server
# ─────────────────────────────────────────────

class SilentHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress request logs

def start_server(directory, port=8765):
    """Start a local HTTP server serving the SCORM package directory."""
    os.chdir(directory)
    server = HTTPServer(('localhost', port), SilentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port

# ─────────────────────────────────────────────
# SCORM Entry Point Detection
# ─────────────────────────────────────────────

def find_entry_point(extract_dir):
    """Find the SCORM launch URL from imsmanifest.xml first, then fallback heuristics."""
    extract_dir = Path(extract_dir)

    # 1. Parse imsmanifest.xml for the launch href
    manifest = extract_dir / 'imsmanifest.xml'
    if manifest.exists():
        try:
            from xml.etree import ElementTree as ET
            tree = ET.parse(str(manifest))
            root = tree.getroot()
            for elem in root.iter():
                tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                if tag == 'resource':
                    href = elem.get('href', '')
                    if href and href.lower().endswith(('.html', '.htm')):
                        candidate = extract_dir / href
                        if candidate.exists():
                            return href
        except Exception:
            pass

    # 2. Priority order: LMS html5 index > LMS index > story html5 > story
    candidates = [
        'index_lms_html5.html',
        'index_lms.html',
        'story_html5.html',
        'story.html',
        'index.html',
    ]
    for candidate in candidates:
        if (extract_dir / candidate).exists():
            return candidate

    # 3. Root-level HTML with "index" in name
    for f in extract_dir.glob('*.html'):
        if 'index' in f.name.lower():
            return f.name

    # 4. Recurse into subdirectories for index*.html
    for f in sorted(extract_dir.rglob('index*.html')):
        return str(f.relative_to(extract_dir))

    return None

# ─────────────────────────────────────────────
# Vision Analysis (Claude)
# ─────────────────────────────────────────────

def resize_screenshot(screenshot_bytes, max_width=1024):
    """Resize screenshot to reduce API payload size."""
    try:
        import struct, zlib
        # Quick PNG width check — if small enough, skip resize
        if len(screenshot_bytes) < 200000:  # under 200KB, fine as-is
            return screenshot_bytes
        # Try PIL if available
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(screenshot_bytes))
            w, h = img.size
            if w > max_width:
                ratio = max_width / w
                img = img.resize((max_width, int(h * ratio)), Image.LANCZOS)
            out = io.BytesIO()
            img.save(out, format='PNG', optimize=True)
            return out.getvalue()
        except ImportError:
            return screenshot_bytes
    except Exception:
        return screenshot_bytes

def analyze_screenshot(client, screenshot_bytes, context=""):
    """Use Claude vision to understand what's on screen and what to do next."""
    screenshot_bytes = resize_screenshot(screenshot_bytes)
    img_b64 = base64.standard_b64encode(screenshot_bytes).decode()

    prompt = f"""You are navigating through an e-learning course as a learner.

{f'Context so far: {context}' if context else ''}

Look at this screenshot of the current course slide/screen.

Respond with JSON only:
{{
  "screen_type": "intro|content|quiz|video|interaction|completion|other",
  "slide_title": "title if visible",
  "content_summary": "what this slide is teaching in 1-2 sentences",
  "has_next_button": true/false,
  "has_quiz": true/false,
  "quiz_question": "the question text if this is a quiz",
  "quiz_options": ["option A", "option B", ...],
  "best_answer": "the correct or best answer based on the content",
  "action": "click_next|answer_quiz|scroll|wait|done",
  "action_target": "describe where to click or what to do"
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": img_b64
                    }
                },
                {"type": "text", "text": prompt}
            ]
        }]
    )

    raw = response.content[0].text.strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
    try:
        return json.loads(raw)
    except:
        return {"content_summary": raw, "action": "click_next", "screen_type": "content"}

# ─────────────────────────────────────────────
# Main Player
# ─────────────────────────────────────────────

def play_scorm(zip_path, use_vision=True, max_slides=30, screenshots_dir=None):
    """
    Main function: extract, serve, navigate, report.
    Returns a structured report dict.
    """
    from playwright.sync_api import sync_playwright

    report = {
        'source_file': str(zip_path),
        'slides': [],
        'scorm_data': {},
        'interactions': {},
        'completion_status': 'unknown',
        'score': None,
        'total_time_ms': 0,
        'course_summary': '',
        'skills_observed': [],
        'errors': []
    }

    extract_dir = tempfile.mkdtemp(prefix='scorm_play_')

    try:
        # 1. Extract ZIP
        print("📦 Extracting SCORM package...")
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(extract_dir)

        # 2. Find entry point
        entry = find_entry_point(extract_dir)
        if not entry:
            report['errors'].append('No HTML entry point found')
            return report
        print(f"🎯 Entry point: {entry}")

        # 3. Start local server
        print("🌐 Starting local HTTP server...")
        server, port = start_server(extract_dir)
        url = f"http://localhost:{port}/{entry}"
        print(f"   Serving at: {url}")

        # 4. Launch browser + navigate
        print("🚀 Launching browser...")
        client = anthropic.Anthropic() if use_vision else None

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--disable-web-security',           # allow cross-origin iframe API access
                    '--disable-site-isolation-trials',  # prevent iframe sandboxing
                    '--allow-running-insecure-content',
                    '--disable-features=IsolateOrigins,site-per-process',
                ]
            )
            context_browser = browser.new_context(
                viewport={'width': 1280, 'height': 720},
                bypass_csp=True,  # bypass Content Security Policy so shim injects everywhere
            )

            # Inject SCORM shim on every page/frame load
            context_browser.add_init_script(SCORM_API_SHIM)

            page = context_browser.new_page()

            # Also force-inject into frames as they navigate (belt + suspenders)
            def inject_into_frame(frame):
                try:
                    frame.evaluate(SCORM_API_SHIM)
                except:
                    pass

            page.on('framenavigated', lambda frame: inject_into_frame(frame))

            print(f"🔗 Opening course...")
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            time.sleep(2)  # let JS initialize

            slide_count = 0
            context_str = ""
            all_content = []

            print(f"▶️  Starting course navigation (max {max_slides} slides)...")

            while slide_count < max_slides:
                slide_count += 1
                print(f"   Slide {slide_count}...", end=' ', flush=True)

                # Screenshot
                screenshot = page.screenshot(full_page=False)

                if screenshots_dir:
                    Path(screenshots_dir).mkdir(exist_ok=True)
                    Path(screenshots_dir, f"slide_{slide_count:03d}.png").write_bytes(screenshot)

                slide_data = {'slide': slide_count, 'content': '', 'action_taken': ''}

                if use_vision and client:
                    try:
                        analysis = analyze_screenshot(client, screenshot, context_str)
                        slide_data.update(analysis)
                        content = analysis.get('content_summary', '')
                        slide_data['content'] = content
                        all_content.append(content)
                        context_str = '; '.join(all_content[-5:])  # last 5 slides as context

                        print(f"{analysis.get('screen_type', '?')} — {content[:60]}...")

                        action = analysis.get('action', 'click_next')

                        if action == 'done' or analysis.get('screen_type') == 'completion':
                            slide_data['action_taken'] = 'completed'
                            report['slides'].append(slide_data)
                            break

                        elif action == 'answer_quiz' and analysis.get('best_answer'):
                            # Try to click the best answer option
                            answer_text = analysis.get('best_answer', '')
                            try:
                                # Look for clickable elements containing the answer
                                page.get_by_text(answer_text, exact=False).first.click(timeout=3000)
                                slide_data['action_taken'] = f'answered: {answer_text}'
                                time.sleep(1)
                                # Then click next
                                _click_next(page)
                            except:
                                _click_next(page)
                                slide_data['action_taken'] = 'click_next (answer failed)'

                        elif action == 'click_next':
                            success = _click_next(page)
                            slide_data['action_taken'] = 'click_next' if success else 'no_next_found'
                            if not success:
                                print("   ⚠️  No next button found — ending")
                                report['slides'].append(slide_data)
                                break

                        else:
                            _click_next(page)
                            slide_data['action_taken'] = 'click_next (fallback)'

                    except Exception as e:
                        print(f"vision error: {e}")
                        slide_data['content'] = f'[vision error: {e}]'
                        _click_next(page)
                        slide_data['action_taken'] = 'click_next (error recovery)'

                else:
                    # No vision — just click next blindly
                    success = _click_next(page)
                    slide_data['action_taken'] = 'click_next'
                    print("navigated")
                    if not success:
                        break

                report['slides'].append(slide_data)
                time.sleep(1.5)

            # 5. Collect SCORM data — sweep all frames (course may live in iframe)
            print("\n📊 Collecting SCORM data...")
            all_frames = page.frames
            print(f"   Checking {len(all_frames)} frame(s)...")
            for frame in all_frames:
                try:
                    scorm_raw = frame.evaluate("window.__SCORM_REPORT__ ? window.__SCORM_REPORT__() : '{}'")
                    scorm_data = json.loads(scorm_raw)
                    if scorm_data.get('data') and len(scorm_data['data']) > 0:
                        print(f"   ✅ SCORM data found in frame: {frame.url[:80]}")
                        report['scorm_data'] = scorm_data.get('data', {})
                        report['interactions'] = scorm_data.get('interactions', {})
                        report['total_time_ms'] = scorm_data.get('elapsedMs', 0)
                        break
                    elif scorm_data.get('log') and len(scorm_data.get('log', [])) > 0:
                        # Log exists but data may be sparse — still capture it
                        print(f"   ℹ️  SCORM log found (sparse data) in frame: {frame.url[:80]}")
                        if not report['scorm_data']:
                            report['scorm_data'] = scorm_data.get('data', {})
                            report['scorm_log'] = scorm_data.get('log', [])
                            report['total_time_ms'] = scorm_data.get('elapsedMs', 0)
                except Exception as e:
                    pass
            if not report['scorm_data']:
                print("   ⚠️  No SCORM API calls captured (course may not communicate with LMS)")

            browser.close()
            server.shutdown()

        # 6. Extract key metrics from SCORM data
        d = report['scorm_data']
        report['completion_status'] = (
            d.get('cmi.core.lesson_status') or
            d.get('cmi.completion_status') or
            'unknown'
        )
        try:
            report['score'] = float(
                d.get('cmi.core.score.raw') or
                d.get('cmi.score.raw') or
                0
            )
        except:
            report['score'] = None

        # 7. Generate final summary with Claude
        if use_vision and client and all_content:
            print("🧠 Generating course summary...")
            summary_prompt = f"""You just completed an e-learning course. Here's what was on each slide:

{chr(10).join(f'{i+1}. {c}' for i, c in enumerate(all_content) if c)}

SCORM completion status: {report['completion_status']}
Score: {report['score']}
Interactions captured: {len(report['interactions'])}

Provide a JSON response:
{{
  "course_summary": "3-4 sentence summary of what this course taught",
  "skills_observed": ["skill1", "skill2", ...],
  "target_audience": "who this is for",
  "content_quality": "high|medium|low",
  "content_quality_notes": "observations about quality, accuracy, engagement",
  "ai_readiness_score": <0-100>,
  "ai_readiness_notes": "what metadata is missing or present"
}}"""

            try:
                resp = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=512,
                    messages=[{"role": "user", "content": summary_prompt}]
                )
                raw = resp.content[0].text.strip()
                if raw.startswith('```'):
                    raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
                summary_data = json.loads(raw)
                report['course_summary'] = summary_data.get('course_summary', '')
                report['skills_observed'] = summary_data.get('skills_observed', [])
                report['ai_analysis'] = summary_data
            except Exception as e:
                report['errors'].append(f'Summary generation error: {e}')

    except Exception as e:
        report['errors'].append(f'Fatal error: {e}')
        import traceback
        report['errors'].append(traceback.format_exc())
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)

    return report


def _click_next(page):
    """Try various strategies to click the Next/Continue button."""
    selectors = [
        # Common next button patterns
        'button:has-text("Next")',
        'button:has-text("Continue")',
        'button:has-text("next")',
        'a:has-text("Next")',
        '[aria-label="Next"]',
        '[aria-label="Continue"]',
        '.next-btn',
        '.btn-next',
        '#next',
        '#btnNext',
        # Storyline-specific
        '[id*="next"]',
        '[class*="next"]',
        # Generic forward navigation
        'button:has-text(">")',
        '[title="Next"]',
        '[title="Continue"]',
    ]

    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=500):
                btn.click(timeout=2000)
                time.sleep(0.5)
                return True
        except:
            continue

    # Last resort: try clicking the right side of the screen (Storyline navigation area)
    try:
        page.mouse.click(1200, 680)
        return True
    except:
        pass

    return False


def main():
    parser = argparse.ArgumentParser(description='SCORM Player Agent — navigate and report on SCORM courses')
    parser.add_argument('scorm_file', help='Path to SCORM .zip file')
    parser.add_argument('--output', help='Output JSON file path (default: stdout)')
    parser.add_argument('--screenshots', help='Directory to save slide screenshots')
    parser.add_argument('--no-vision', action='store_true', help='Skip vision analysis (blind navigation)')
    parser.add_argument('--max-slides', type=int, default=30, help='Max slides to navigate (default: 30)')
    args = parser.parse_args()

    use_vision = not args.no_vision
    if use_vision and not os.environ.get('ANTHROPIC_API_KEY'):
        print("⚠️  No ANTHROPIC_API_KEY set. Running without vision analysis.")
        use_vision = False

    report = play_scorm(
        args.scorm_file,
        use_vision=use_vision,
        max_slides=args.max_slides,
        screenshots_dir=args.screenshots
    )

    output = json.dumps(report, indent=2)

    if args.output:
        Path(args.output).write_text(output)
        print(f"\n✅ Report saved to: {args.output}")
    else:
        print(output)


if __name__ == '__main__':
    main()
