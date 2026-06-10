"""Headless SCORM course player.

Launches the course in a real (headless) Chromium, injects a SCORM 1.2 +
2004 API shim so the course believes it is talking to an LMS, then navigates
like a learner: clicks Start/Next, answers quizzes, scrolls Rise lessons,
resolves drag-drops, and records EVERYTHING the course reports back —
completion status, score, session time, every cmi.* write, every interaction.

No LLM required: navigation and quiz-answering are deterministic. Vision
analysis of the captured screenshots is layered on separately (scormlib.llm).
"""

import json
import os
import shutil
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# SCORM API shim — captures all LMS traffic, exposes __SCORM_REPORT__()
# ─────────────────────────────────────────────────────────────────────────────
SCORM_API_SHIM = r"""
(function() {
  if (window.__SCORM_CAPTURED__) return;
  window.__SCORM_CAPTURED__ = true;

  var _data = {};
  var _interactions = {};
  var _log = [];
  var _sessionStart = Date.now();
  var _state = { initialized: false, finished: false, commits: 0 };

  function logEntry(fn, key, value) {
    if (_log.length < 5000) _log.push({ fn: fn, key: key, value: value, t: Date.now() - _sessionStart });
  }
  function trackInteraction(key, value) {
    if (!key || key.indexOf('cmi.interactions') !== 0) return;
    var parts = key.split('.');
    if (parts.length >= 4) {
      var idx = parts[2];
      if (!_interactions[idx]) _interactions[idx] = {};
      _interactions[idx][parts.slice(3).join('.')] = value;
    }
  }

  var API = {
    LMSInitialize: function() { _state.initialized = true; logEntry('LMSInitialize'); return 'true'; },
    LMSFinish: function() { _state.finished = true; logEntry('LMSFinish'); return 'true'; },
    LMSGetValue: function(key) {
      var val = _data[key] || '';
      if (key === 'cmi.core.lesson_mode') val = _data[key] || 'normal';
      logEntry('LMSGetValue', key, val);
      return val;
    },
    LMSSetValue: function(key, value) {
      _data[key] = String(value);
      logEntry('LMSSetValue', key, String(value));
      trackInteraction(key, String(value));
      return 'true';
    },
    LMSCommit: function() { _state.commits++; logEntry('LMSCommit'); return 'true'; },
    LMSGetLastError: function() { return '0'; },
    LMSGetErrorString: function() { return 'No error'; },
    LMSGetDiagnostic: function() { return ''; }
  };

  var API_1484_11 = {
    Initialize: function() { _state.initialized = true; logEntry('Initialize'); return 'true'; },
    Terminate: function() { _state.finished = true; logEntry('Terminate'); return 'true'; },
    GetValue: function(key) {
      var val = _data[key] || '';
      if (key === 'cmi.mode') val = _data[key] || 'normal';
      logEntry('GetValue', key, val);
      return val;
    },
    SetValue: function(key, value) {
      _data[key] = String(value);
      logEntry('SetValue', key, String(value));
      trackInteraction(key, String(value));
      return 'true';
    },
    Commit: function() { _state.commits++; logEntry('Commit'); return 'true'; },
    GetLastError: function() { return '0'; },
    GetErrorString: function() { return 'No error'; },
    GetDiagnostic: function() { return ''; }
  };

  window.API = API;
  window.API_1484_11 = API_1484_11;
  try {
    var w = window;
    for (var i = 0; i < 8 && w.parent && w.parent !== w; i++) {
      w = w.parent;
      try { if (!w.API) w.API = API; if (!w.API_1484_11) w.API_1484_11 = API_1484_11; } catch(e) { break; }
    }
  } catch(e) {}

  window.__SCORM_REPORT__ = function() {
    return JSON.stringify({
      data: _data,
      interactions: _interactions,
      log: _log.slice(-500),
      logCount: _log.length,
      state: _state,
      elapsedMs: Date.now() - _sessionStart
    });
  };
})();
"""

# Click the first visible element whose text/aria-label matches a regex.
CLICK_BY_TEXT_JS = r"""
(pattern) => {
  const rx = new RegExp(pattern, 'i');
  const els = document.querySelectorAll(
    'button, a, [role="button"], input[type=submit], input[type=button], [class*="btn"]');
  for (const el of els) {
    const label = ((el.innerText || '') + ' ' + (el.value || '') + ' ' +
      (el.getAttribute('aria-label') || '') + ' ' + (el.title || '')).trim();
    const attrs = (el.className + ' ' + el.id);
    const r = el.getBoundingClientRect();
    if (r.width > 2 && r.height > 2 && (rx.test(label) || rx.test(attrs))) {
      el.click();
      return label.slice(0, 60) || attrs.slice(0, 60);
    }
  }
  return null;
}
"""

NEXT_PATTERN = r'^\s*(next|continue|start( course)?|begin|launch|proceed|forward|finish|>|→)\b|(^|\s)(next|continue)($|\s)|nextbutton|btn-?next'
SUBMIT_PATTERN = r'submit|check( answer)?|^done$|^ok$|confirm'

# Answer any visible quiz inputs: first radio per group, first checkbox, fill text.
ANSWER_QUIZ_JS = r"""
() => {
  let acted = 0;
  const seen = new Set(
    [...document.querySelectorAll('input[type=radio]:checked')].map(r => r.name || 'g'));
  for (const r of document.querySelectorAll('input[type=radio]')) {
    const g = r.name || 'g';
    const rect = r.getBoundingClientRect();
    if (!seen.has(g) && rect.width + rect.height > 0) {
      seen.add(g);
      r.click();
      r.checked = true;
      r.dispatchEvent(new Event('change', {bubbles: true}));
      acted++;
    }
  }
  const checked = document.querySelector('input[type=checkbox]:checked');
  const boxes = document.querySelectorAll('input[type=checkbox]');
  if (boxes.length && !checked) { boxes[0].click(); acted++; }
  for (const t of document.querySelectorAll('input[type=text]:not([readonly])')) {
    if (!t.value && t.getBoundingClientRect().width > 0) {
      t.value = 'answer';
      t.dispatchEvent(new Event('input', {bubbles: true}));
      acted++;
    }
  }
  return acted;
}
"""

# Extract quiz structure: the question text + option labels per radio group.
EXTRACT_QUIZ_JS = r"""
() => {
  const groups = {};
  for (const r of document.querySelectorAll('input[type=radio]')) {
    const g = r.name || 'g';
    if (!groups[g]) groups[g] = [];
    let label = '';
    if (r.id) { const l = document.querySelector('label[for="' + r.id + '"]'); if (l) label = l.innerText; }
    if (!label) { const l = r.closest('label'); if (l) label = l.innerText; }
    if (!label && r.parentElement) label = r.parentElement.innerText;
    groups[g].push((label || '').replace(/\s+/g, ' ').trim().slice(0, 250));
  }
  let question = '';
  const first = document.querySelector('input[type=radio]');
  if (first) {
    let el = first.closest('form, fieldset, section, div') || document.body;
    for (let i = 0; i < 6 && el; i++) {
      const lines = (el.innerText || '').split('\n').map(t => t.trim());
      const qm = lines.find(t => t.endsWith('?') && t.length > 12);
      if (qm) { question = qm; break; }
      el = el.parentElement;
    }
  }
  return {question, groups};
}
"""

# Select a specific option within a radio group.
SELECT_OPTION_JS = r"""
(args) => {
  const radios = [...document.querySelectorAll('input[type=radio]')]
    .filter(r => (r.name || 'g') === args.group);
  const r = radios[args.index];
  if (!r) return false;
  r.click();
  r.checked = true;
  r.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
}
"""

# Does the visible page report a wrong answer with a retry path?
WRONG_ANSWER_JS = r"""
() => {
  const text = (document.body && document.body.innerText || '').slice(0, 4000);
  const wrong = /incorrect|not quite|wrong answer|try again/i.test(text);
  const retry = [...document.querySelectorAll('button, a, [role="button"]')]
    .some(b => /try again|retry/i.test(b.innerText || ''));
  const radiosLive = [...document.querySelectorAll('input[type=radio]')]
    .some(r => !r.disabled);
  return {wrong, canRetry: retry || (wrong && radiosLive)};
}
"""

INTERACTION_TYPE_JS = r"""
() => {
  const d = document;
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  if ([...d.querySelectorAll('input[type=radio], input[type=checkbox]')].some(visible)) return 'quiz';
  if (d.querySelector('[draggable=true], [class*="drag" i][class*="item" i], [class*="dropzone" i], [class*="drop-target" i]')) return 'drag-drop';
  if (d.querySelector('map area, [class*="hotspot" i]')) return 'hotspot';
  if (d.querySelector('video')) return 'video';
  const btns = [...d.querySelectorAll('button, [role="button"]')].filter(visible)
    .filter(b => !/next|prev|back|submit|menu|volume|play|pause|seek|caption/i.test(b.className + ' ' + (b.getAttribute('aria-label') || '') + ' ' + (b.innerText || '')));
  if (btns.length >= 2 && d.querySelector('img')) return 'scenario';
  return 'standard';
}
"""

DOM_FINGERPRINT_JS = "() => (document.body && document.body.innerHTML.length) || 0"

FORCE_DRAG_JS = r"""
() => {
  try {
    const drags = document.querySelectorAll('[draggable="true"], .drag-item, .draggable, [class*="drag-item"]');
    const drops = document.querySelectorAll('.drop-target, .drop-zone, [class*="drop-target"], [class*="drop-zone"], [class*="dropzone"]');
    drags.forEach((drag, i) => {
      const target = drops[i] || drops[drops.length - 1];
      if (!target) return;
      const rect = target.getBoundingClientRect();
      ['dragstart', 'dragover', 'drop', 'dragend'].forEach(name => {
        const evt = new DragEvent(name, {bubbles: true, cancelable: true,
          clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2});
        (name === 'dragstart' || name === 'dragend' ? drag : target).dispatchEvent(evt);
      });
    });
    return drags.length;
  } catch (e) { return -1; }
}
"""

RISE_SCROLL_JS = r"""
(dy) => {
  const el = Array.from(document.querySelectorAll('*')).find(e => {
    const s = window.getComputedStyle(e);
    return (s.overflow === 'auto' || s.overflow === 'scroll' ||
            s.overflowY === 'auto' || s.overflowY === 'scroll') &&
           e.scrollHeight > e.clientHeight + 50;
  });
  if (el) {
    el.scrollBy(0, dy);
    return {top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight, container: true};
  }
  window.scrollBy(0, dy);
  return {top: window.scrollY, height: document.body.scrollHeight, client: window.innerHeight, container: false};
}
"""

FORCE_COMPLETE_JS = r"""
() => {
  const tryAll = (api, calls) => { if (!api) return; calls.forEach(([fn, ...args]) => { try { api[fn](...args); } catch (e) {} }); };
  for (const w of [window, window.parent]) {
    try {
      tryAll(w.API, [['LMSSetValue', 'cmi.core.lesson_status', 'completed'],
                     ['LMSSetValue', 'cmi.core.score.raw', '100'], ['LMSCommit', '']]);
      tryAll(w.API_1484_11, [['SetValue', 'cmi.completion_status', 'completed'],
                             ['SetValue', 'cmi.success_status', 'passed'],
                             ['SetValue', 'cmi.score.raw', '100'], ['Commit', '']]);
    } catch (e) {}
  }
  return 'done';
}
"""


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def start_server(directory):
    """Serve a directory on an ephemeral localhost port (no chdir)."""
    handler = partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def resolve_browser(pw, headless=True, launch_args=None):
    """Launch Chromium: Playwright-managed first, then system/env fallbacks."""
    launch_args = launch_args or []
    errors = []
    try:
        return pw.chromium.launch(headless=headless, args=launch_args)
    except Exception as e:
        errors.append(str(e))

    candidates = [os.environ.get('SCORM_BROWSER_PATH'),
                  '/opt/chrome-headless-shell-linux64/chrome-headless-shell',
                  shutil.which('chromium'), shutil.which('chromium-browser'),
                  shutil.which('google-chrome'), shutil.which('google-chrome-stable'),
                  shutil.which('chrome-headless-shell')]
    # Any browser already in a Playwright cache (possibly a different version)
    cache = Path(os.environ.get('PLAYWRIGHT_BROWSERS_PATH',
                                Path.home() / '.cache' / 'ms-playwright'))
    if cache.is_dir():
        candidates += [str(p) for p in
                       sorted(cache.glob('chromium-*/chrome-linux/chrome'), reverse=True)]
        candidates += [str(p) for p in
                       sorted(cache.glob('chromium_headless_shell-*/chrome-linux/headless_shell'),
                              reverse=True)]
    for path in candidates:
        if path and Path(path).exists():
            try:
                return pw.chromium.launch(headless=headless, args=launch_args,
                                          executable_path=path)
            except Exception as e:
                errors.append(f'{path}: {e}')
    raise RuntimeError(
        'No Chromium available. Run `playwright install chromium` or set '
        'SCORM_BROWSER_PATH to a Chrome/Chromium binary. Tried: '
        + ' | '.join(errors[:3]))


def detect_course_type(extract_dir, organizations=None):
    """rise (scroll-based) vs storyline (click-based) vs generic."""
    extract_dir = Path(extract_dir)
    if (extract_dir / 'scormcontent' / 'index.html').exists() or \
            list(extract_dir.rglob('scormcontent/index.html')):
        return 'rise'
    org_str = json.dumps(organizations or [])
    if 'articulate_rise' in org_str.lower():
        return 'rise'
    if (extract_dir / 'story.html').exists() or (extract_dir / 'story_html5.html').exists() \
            or list(extract_dir.rglob('html5/data/js/data.js')):
        return 'storyline'
    return 'generic'


class _PageDriver:
    """Frame-aware helpers around a Playwright page."""

    def __init__(self, page):
        self.page = page

    def eval_all_frames(self, js, arg=None):
        """Evaluate JS in every frame, return list of (frame, result)."""
        results = []
        for frame in self.page.frames:
            try:
                results.append((frame, frame.evaluate(js) if arg is None
                                else frame.evaluate(js, arg)))
            except Exception:
                pass
        return results

    def inject_shim(self):
        self.eval_all_frames(SCORM_API_SHIM)

    def fingerprint(self):
        vals = [v for _f, v in self.eval_all_frames(DOM_FINGERPRINT_JS)
                if isinstance(v, (int, float))]
        return max(vals) if vals else 0

    def interaction_type(self):
        for _f, val in self.eval_all_frames(INTERACTION_TYPE_JS):
            if val and val != 'standard':
                return val
        return 'standard'

    def click_text(self, pattern):
        for _f, val in self.eval_all_frames(CLICK_BY_TEXT_JS, pattern):
            if val:
                return val
        return None

    def answer_quiz(self):
        total = 0
        for _f, val in self.eval_all_frames(ANSWER_QUIZ_JS):
            if isinstance(val, int):
                total += val
        return total

    def extract_quiz(self):
        """Return (frame, {'question':…, 'groups': {name: [labels]}}) or (None, None)."""
        for frame, val in self.eval_all_frames(EXTRACT_QUIZ_JS):
            if isinstance(val, dict) and val.get('groups'):
                return frame, val
        return None, None

    def select_option(self, frame, group, index):
        try:
            return frame.evaluate(SELECT_OPTION_JS, {'group': group, 'index': index})
        except Exception:
            return False

    def wrong_answer_state(self):
        for _f, val in self.eval_all_frames(WRONG_ANSWER_JS):
            if isinstance(val, dict) and val.get('wrong'):
                return val
        return {'wrong': False, 'canRetry': False}

    def scorm_report(self):
        """Collect the richest shim report across frames."""
        best = {}
        for _f, raw in self.eval_all_frames(
                "() => window.__SCORM_REPORT__ ? window.__SCORM_REPORT__() : null"):
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if len(data.get('data', {})) > len(best.get('data', {})) or \
                    (not best and data.get('logCount')):
                best = data
        return best

    def completion_status(self):
        report = self.scorm_report()
        d = report.get('data', {})
        status = (d.get('cmi.core.lesson_status') or d.get('cmi.completion_status')
                  or '').lower()
        finished = report.get('state', {}).get('finished', False)
        return status, finished


def _runtime_summary(report):
    d = report.get('data', {})
    score_raw = d.get('cmi.core.score.raw') or d.get('cmi.score.raw')
    try:
        score_raw = float(score_raw) if score_raw not in (None, '') else None
    except (TypeError, ValueError):
        score_raw = None
    return {
        'completion_status': (d.get('cmi.core.lesson_status')
                              or d.get('cmi.completion_status') or 'unknown'),
        'success_status': d.get('cmi.success_status', ''),
        'score_raw': score_raw,
        'score_max': d.get('cmi.core.score.max') or d.get('cmi.score.max') or '',
        'score_min': d.get('cmi.core.score.min') or d.get('cmi.score.min') or '',
        'session_time': (d.get('cmi.core.session_time')
                         or d.get('cmi.session_time') or ''),
        'lesson_location': (d.get('cmi.core.lesson_location')
                            or d.get('cmi.location') or ''),
        'suspend_data_chars': len(d.get('cmi.suspend_data', '')),
        'interactions': report.get('interactions', {}),
        'interaction_count': len(report.get('interactions', {})),
        'api_call_count': report.get('logCount', 0),
        'lms_initialized': report.get('state', {}).get('initialized', False),
        'lms_finish_called': report.get('state', {}).get('finished', False),
        'commits': report.get('state', {}).get('commits', 0),
        'elapsed_ms': report.get('elapsedMs', 0),
        'cmi_data': d,
    }


def play_package(extract_dir, entry_href, screenshots_dir=None, max_slides=40,
                 organizations=None, headless=True, slide_settle_s=1.0,
                 force_complete_on_stall=True, verbose=False, quiz_solver='auto'):
    """Play an extracted SCORM package end to end.

    Returns dict with course_type, slides[], runtime (SCORM data captured),
    completion info, stall_count, screenshots list.
    """
    from playwright.sync_api import sync_playwright

    extract_dir = Path(extract_dir)
    log = (lambda *a: print(*a, flush=True)) if verbose else (lambda *a: None)
    if quiz_solver == 'auto':
        quiz_solver = make_llm_quiz_solver()
        if quiz_solver:
            log('  quiz solver: LLM-backed (answers chosen by AI)')
    course_type = detect_course_type(extract_dir, organizations)
    if screenshots_dir:
        screenshots_dir = Path(screenshots_dir)
        screenshots_dir.mkdir(parents=True, exist_ok=True)

    result = {
        'course_type': course_type,
        'entry_point': entry_href,
        'slides': [],
        'stall_count': 0,
        'completed_naturally': False,
        'forced_completion': False,
        'runtime': {},
        'error': None,
    }

    server, port = start_server(extract_dir)
    rise_file_url = None
    sc_index = extract_dir / 'scormcontent' / 'index.html'
    if course_type == 'rise' and sc_index.exists():
        # Rise allows file:// directly and skips its cloud-LMS handshake there
        rise_file_url = f'file://{sc_index}'

    try:
        launch_args = ['--disable-web-security', '--disable-site-isolation-trials',
                       '--allow-running-insecure-content',
                       '--disable-features=IsolateOrigins,site-per-process']
        if rise_file_url:
            launch_args.append('--allow-file-access-from-files')

        with sync_playwright() as pw:
            browser = resolve_browser(pw, headless=headless, launch_args=launch_args)
            context = browser.new_context(viewport={'width': 1280, 'height': 720},
                                          bypass_csp=True)
            context.add_init_script(SCORM_API_SHIM)
            page = context.new_page()
            driver = _PageDriver(page)
            page.on('framenavigated',
                    lambda frame: _safe_eval(frame, SCORM_API_SHIM))

            url = rise_file_url or f'http://127.0.0.1:{port}/{entry_href}'
            log(f'  → opening {url}')
            try:
                page.goto(url, timeout=30000, wait_until='domcontentloaded')
            except Exception:
                pass
            time.sleep(2.0)
            driver.inject_shim()

            def snap(index, interaction, extra=None):
                entry = {'index': index, 'interaction_type': interaction,
                         'stalled': False}
                if screenshots_dir:
                    path = screenshots_dir / f'slide_{index:03d}.png'
                    try:
                        page.screenshot(path=str(path))
                        entry['screenshot_path'] = str(path)
                    except Exception:
                        pass
                if extra:
                    entry.update(extra)
                result['slides'].append(entry)
                return entry

            if course_type == 'rise':
                _play_rise(driver, snap, max_slides, log)
            else:
                _play_clickthrough(driver, snap, max_slides, slide_settle_s,
                                   result, log, quiz_solver=quiz_solver)

            # Final SCORM state
            report = driver.scorm_report()
            status, finished = driver.completion_status()
            if status in ('completed', 'passed') or finished:
                result['completed_naturally'] = True
            elif force_complete_on_stall:
                # Last resort: ask the course politely, then force via API
                driver.click_text(r'finish|close|exit|complete')
                time.sleep(1.0)
                status, finished = driver.completion_status()
                if status in ('completed', 'passed') or finished:
                    result['completed_naturally'] = True
                else:
                    driver.eval_all_frames(FORCE_COMPLETE_JS)
                    time.sleep(0.5)
                    result['forced_completion'] = True
                report = driver.scorm_report()

            result['runtime'] = _runtime_summary(report)
            context.close()
            browser.close()
    except Exception as e:
        result['error'] = f'Player error: {e}'
    finally:
        server.shutdown()

    result['screenshots'] = [s['screenshot_path'] for s in result['slides']
                             if s.get('screenshot_path')]
    result['total_slides_detected'] = len(result['slides'])
    result['screenshots_captured'] = len(result['screenshots'])
    return result


def _safe_eval(frame, js):
    try:
        frame.evaluate(js)
    except Exception:
        pass


def make_llm_quiz_solver(provider='claude', api_key=None):
    """Build a quiz solver backed by an LLM. Returns None if no key available."""
    from . import llm as llm_mod
    if not (llm_mod.llm_available(provider) or api_key):
        return None

    def solver(question, options):
        numbered = '\n'.join(f'{i}: {opt}' for i, opt in enumerate(options))
        prompt = (
            'You are taking an e-learning quiz. Pick the best answer.\n\n'
            f'Question: {question or "(question text not captured — use judgment)"}\n\n'
            f'Options:\n{numbered}\n\n'
            'Reply with ONLY JSON: {"answer_index": <0-based index>}')
        raw = llm_mod.complete(prompt, provider=provider, api_key=api_key,
                               max_tokens=50)
        return int(llm_mod.parse_json_response(raw).get('answer_index'))

    return solver


def _handle_quiz(driver, quiz_solver, max_retries=3):
    """Answer the visible quiz. Uses the LLM solver when available, falls back
    to first-option selection, and rotates answers on a wrong-with-retry state.

    Returns (groups_answered, solved_by)."""
    frame, quiz = driver.extract_quiz()
    chosen = {}
    solved_by = 'first-option'
    if frame and quiz and quiz_solver:
        for group, options in quiz['groups'].items():
            if len(options) < 2:
                continue
            try:
                idx = quiz_solver(quiz.get('question', ''), options)
            except Exception:
                idx = None
            if isinstance(idx, int) and 0 <= idx < len(options):
                if driver.select_option(frame, group, idx):
                    chosen[group] = idx
                    solved_by = 'llm'

    answered = driver.answer_quiz()  # fills any groups the solver didn't reach
    total = len(chosen) + answered
    time.sleep(0.4)
    driver.click_text(SUBMIT_PATTERN)
    time.sleep(0.8)

    # Wrong answer with a retry path? Rotate options.
    for _attempt in range(max_retries):
        state = driver.wrong_answer_state()
        if not (state.get('wrong') and state.get('canRetry')):
            break
        driver.click_text(r'try again|retry')
        time.sleep(0.5)
        frame, quiz = driver.extract_quiz()
        if not frame or not quiz:
            break
        for group, options in quiz['groups'].items():
            nxt = (chosen.get(group, 0) + 1) % max(len(options), 1)
            if driver.select_option(frame, group, nxt):
                chosen[group] = nxt
                solved_by = solved_by + '+rotate' if 'rotate' not in solved_by else solved_by
        driver.click_text(SUBMIT_PATTERN)
        time.sleep(0.8)

    return total, solved_by


def _play_clickthrough(driver, snap, max_slides, settle, result, log,
                       quiz_solver=None):
    """Storyline-style click-based navigation with quiz answering."""
    prev_fp = driver.fingerprint()
    no_progress = 0

    for index in range(1, max_slides + 1):
        time.sleep(settle)
        driver.inject_shim()
        interaction = driver.interaction_type()
        entry = snap(index, interaction)
        log(f'  slide {index}: {interaction}')

        # Completed?
        status, finished = driver.completion_status()
        if status in ('completed', 'passed', 'failed') or finished:
            entry['note'] = f'course reported {status or "finish"}'
            break

        # Handle interactions before advancing
        if interaction == 'quiz':
            answered, solved_by = _handle_quiz(driver, quiz_solver)
            if answered:
                entry['quiz_answered'] = answered
                entry['quiz_solver'] = solved_by
        elif interaction == 'drag-drop':
            driver.eval_all_frames(FORCE_DRAG_JS)
            time.sleep(0.4)
            driver.click_text(SUBMIT_PATTERN)
            time.sleep(0.5)

        # Advance
        clicked = driver.click_text(NEXT_PATTERN)
        if not clicked:
            try:
                driver.page.keyboard.press('ArrowRight')
            except Exception:
                pass

        # Wait for DOM change
        changed = False
        deadline = time.time() + 3.0
        while time.time() < deadline:
            time.sleep(0.3)
            fp = driver.fingerprint()
            if fp != prev_fp:
                prev_fp = fp
                changed = True
                break

        if not changed:
            # Unlock strategies: interact with everything, then force quiz, then give up
            entry['stalled'] = True
            result['stall_count'] += 1
            unlocked = _unlock_slide(driver, prev_fp)
            if unlocked:
                entry['stalled'] = False
                entry['unlocked'] = True
                prev_fp = driver.fingerprint()
                no_progress = 0
            else:
                no_progress += 1
                if no_progress >= 2:
                    break
        else:
            no_progress = 0


def _unlock_slide(driver, prev_fp):
    """Try progressively heavier strategies to get a locked slide to advance."""
    # 1. answer quiz + submit
    driver.answer_quiz()
    driver.click_text(SUBMIT_PATTERN)
    time.sleep(0.8)
    driver.click_text(NEXT_PATTERN)
    time.sleep(1.0)
    if driver.fingerprint() != prev_fp:
        return True

    # 2. simulate drag-drop completion
    driver.eval_all_frames(FORCE_DRAG_JS)
    time.sleep(0.5)
    driver.click_text(SUBMIT_PATTERN)
    driver.click_text(NEXT_PATTERN)
    time.sleep(1.0)
    if driver.fingerprint() != prev_fp:
        return True

    # 3. click every non-navigation interactive element
    driver.eval_all_frames(r"""
() => {
  const skip = /next|prev|back|submit|menu|exit|close|volume|caption/i;
  let n = 0;
  for (const el of document.querySelectorAll('button, [role="button"], [class*="option"], [class*="choice"], [class*="card"], a')) {
    const label = (el.innerText || '') + ' ' + el.className;
    const r = el.getBoundingClientRect();
    if (r.width > 2 && r.height > 2 && !skip.test(label) && n < 12) { el.click(); n++; }
  }
  return n;
}""")
    time.sleep(1.0)
    driver.click_text(NEXT_PATTERN)
    time.sleep(1.0)
    return driver.fingerprint() != prev_fp


def _play_rise(driver, snap, max_slides, log):
    """Rise-style scroll-based navigation."""
    snap(1, 'scroll')
    started = driver.click_text(r'start course|^start$|begin')
    log(f'  rise start click: {started}')
    time.sleep(2.5)
    driver.inject_shim()

    prev_top = -1
    stall_streak = 0
    for index in range(2, max_slides + 1):
        snap(index, driver.interaction_type())
        infos = driver.eval_all_frames(RISE_SCROLL_JS, 700)
        time.sleep(0.8)
        info = next((v for _f, v in infos if isinstance(v, dict)), {})
        top = info.get('top', 0)
        if top == prev_top:
            stall_streak += 1
            # try continue-style buttons that gate Rise sections
            driver.click_text(r'continue|next lesson')
            time.sleep(0.6)
            if stall_streak >= 3:
                break
        else:
            stall_streak = 0
        prev_top = top
        if info and top + info.get('client', 0) >= info.get('height', 1) - 60:
            snap(index + 1, 'scroll')
            break
