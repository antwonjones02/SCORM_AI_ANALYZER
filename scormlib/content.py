"""Deterministic content mining — extracts human-readable course content
(slide titles, body text, quiz questions, transcripts) from a SCORM package
WITHOUT any LLM or network calls.

Understands: plain HTML SCOs, Articulate Storyline data files
(globalProvideData JS blobs), Articulate Rise embedded course JSON,
caption files (.vtt/.srt), and generic JSON content files."""

import base64
import html as html_mod
import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path

# Keys whose string values we harvest from authoring-tool JSON blobs
_TEXT_KEYS = {'title', 'text', 'alttext', 'caption', 'description', 'heading',
              'paragraph', 'label', 'slidetitle', 'name', 'transcript', 'notes'}
_QUIZ_KEYS = {'question', 'prompt', 'stem'}
_SKIP_VALUES = re.compile(
    r'^(true|false|null|none|\d+|[a-f0-9-]{8,}|#[0-9a-f]{3,8}|rgba?\(.*\)|'
    r'[\w-]+\.(png|jpe?g|gif|svg|mp[34]|woff2?|js|css|html?)|'
    r'(https?|file)://\S+|[A-Za-z0-9+/=_-]{40,})$', re.IGNORECASE)


class _TextCollector(HTMLParser):
    """Collects visible text and headings from HTML, skipping script/style."""

    _SKIP_TAGS = {'script', 'style', 'noscript', 'template', 'head'}
    _HEADING_TAGS = {'h1', 'h2', 'h3'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._heading_tag = None
        self._heading_buf = []
        self.chunks = []
        self.headings = []
        self.page_title = ''
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._HEADING_TAGS:
            self._heading_tag = tag
            self._heading_buf = []
        elif tag == 'title':
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._HEADING_TAGS and self._heading_tag == tag:
            heading = ' '.join(self._heading_buf).strip()
            if heading:
                self.headings.append(heading)
            self._heading_tag = None
        elif tag == 'title':
            self._in_title = False

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title and not self.page_title:
            self.page_title = text
        if self._heading_tag:
            self._heading_buf.append(text)
        self.chunks.append(text)


def extract_html_content(path):
    """Return (text, headings, page_title) for one HTML file."""
    try:
        raw = Path(path).read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return '', [], ''
    parser = _TextCollector()
    try:
        parser.feed(raw)
    except Exception:
        # Fall back to crude tag stripping on parser blowups
        text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', raw,
                      flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        return re.sub(r'\s+', ' ', html_mod.unescape(text)).strip(), [], ''
    text = re.sub(r'\s+', ' ', ' '.join(parser.chunks)).strip()
    return text, parser.headings, parser.page_title


def parse_caption_file(path):
    """Convert a .vtt or .srt caption file to plain text."""
    try:
        raw = Path(path).read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return ''
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line == 'WEBVTT':
            continue
        if re.match(r'^\d+$', line):           # srt cue number
            continue
        if '-->' in line:                       # timestamp line
            continue
        if re.match(r'^(NOTE|STYLE|REGION)\b', line):
            continue
        line = re.sub(r'<[^>]+>', '', line)     # strip cue tags
        lines.append(line)
    # de-duplicate consecutive repeats (rolling captions)
    deduped = [l for i, l in enumerate(lines) if i == 0 or l != lines[i - 1]]
    return ' '.join(deduped).strip()


def _harvest_json(node, texts, questions, depth=0):
    """Recursively pull human-readable strings out of authoring-tool JSON."""
    if depth > 40:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            lk = key.lower()
            if isinstance(value, str):
                val = value.strip()
                if len(val) < 2 or _SKIP_VALUES.match(val):
                    continue
                # Storyline embeds HTML inside text fields sometimes
                if '<' in val and '>' in val:
                    val = re.sub(r'<[^>]+>', ' ', val)
                    val = re.sub(r'\s+', ' ', html_mod.unescape(val)).strip()
                    if len(val) < 2:
                        continue
                if lk in _QUIZ_KEYS:
                    questions.append(val)
                    texts.append(val)
                elif lk in _TEXT_KEYS:
                    texts.append(val)
            else:
                _harvest_json(value, texts, questions, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _harvest_json(item, texts, questions, depth + 1)


_GLOBAL_PROVIDE_RE = re.compile(
    r"globalProvideData\(\s*'(\w+)'\s*,\s*'(.*)'\s*\)", re.DOTALL)


def parse_storyline_js(path):
    """Parse an Articulate Storyline data/js file (globalProvideData wrapper).

    Returns (texts, questions, slide_titles)."""
    texts, questions, slide_titles = [], [], []
    try:
        raw = Path(path).read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return texts, questions, slide_titles
    for kind, payload in _GLOBAL_PROVIDE_RE.findall(raw):
        # The payload is a single-quoted JS string: unescape \' \\ \n \uXXXX
        try:
            unescaped = payload.encode('utf-8').decode('unicode_escape')
        except UnicodeDecodeError:
            unescaped = payload.replace("\\'", "'").replace('\\\\', '\\')
        try:
            data = json.loads(unescaped)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            title = data.get('title')
            if isinstance(title, str) and title.strip() and kind in ('slide', 'data'):
                slide_titles.append(title.strip())
        _harvest_json(data, texts, questions)
    return texts, questions, slide_titles


_RISE_B64_RE = re.compile(
    r'(?:courseData|window\.courseData)\s*=\s*["\']([A-Za-z0-9+/=\s]{100,})["\']')
_JSON_BLOB_RE = re.compile(r'\{"[^"]+"\s*:')


def parse_rise_index(path):
    """Parse an Articulate Rise scormcontent/index.html for embedded course JSON.

    Returns (texts, questions, lesson_titles, course_title, course_description)."""
    texts, questions, lesson_titles = [], [], []
    course_title, course_description = '', ''
    try:
        raw = Path(path).read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return texts, questions, lesson_titles, course_title, course_description

    payloads = []
    m = _RISE_B64_RE.search(raw)
    if m:
        try:
            decoded = base64.b64decode(re.sub(r'\s+', '', m.group(1))).decode(
                'utf-8', errors='ignore')
            payloads.append(decoded)
        except Exception:
            pass

    # Inline JSON assignments (Rise also ships plain JSON in some versions)
    for jsm in re.finditer(r'=\s*(\{"(?:course|share|lessons)".{200,}?\});?\s*\n', raw):
        payloads.append(jsm.group(1))

    for payload in payloads:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        course = data.get('course', data) if isinstance(data, dict) else data
        if isinstance(course, dict):
            if not course_title and isinstance(course.get('title'), str):
                course_title = course['title'].strip()
            if not course_description and isinstance(course.get('description'), str):
                course_description = re.sub(r'<[^>]+>', ' ',
                                            course['description']).strip()
            for lesson in course.get('lessons', []) or []:
                if isinstance(lesson, dict) and isinstance(lesson.get('title'), str):
                    lesson_titles.append(lesson['title'].strip())
        _harvest_json(data, texts, questions)

    return texts, questions, lesson_titles, course_title, course_description


_JS_STRING_RE = re.compile(
    r'''(?P<q>["'])(?P<s>(?:(?!(?P=q))[^\\\n]|\\.){30,400})(?P=q)''')
_SENTENCE_RE = re.compile(r'^[A-Z"“].*[a-z].*(?:[.?!…]|[a-z])\s*$')


def parse_js_strings(path, max_bytes=2_000_000):
    """Harvest sentence-like string literals from hand-written JS files.

    Many SPA-style courses (and quiz engines) keep their instructional text in
    JS string literals. We only accept strings that read like prose: length
    >= 30, several spaces, mostly letters, sentence-shaped. Minified library
    bundles are skipped via an average-line-length heuristic."""
    try:
        p = Path(path)
        if p.stat().st_size > max_bytes:
            return []
        raw = p.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return []
    lines = raw.splitlines() or ['']
    if len(raw) / max(len(lines), 1) > 500:    # minified bundle
        return []
    out = []
    for m in _JS_STRING_RE.finditer(raw):
        s = m.group('s')
        s = s.replace("\\'", "'").replace('\\"', '"').replace('\\n', ' ')
        if '<' in s and '>' in s:
            s = re.sub(r'<[^>]+>', ' ', s)
        s = re.sub(r'\s+', ' ', html_mod.unescape(s)).strip()
        if len(s) < 30 or s.count(' ') < 4:
            continue
        letters = sum(c.isalpha() or c.isspace() for c in s)
        if letters / len(s) < 0.8:
            continue
        if not _SENTENCE_RE.match(s):
            continue
        out.append(s)
    return out


def _looks_like_question(line):
    line = line.strip()
    return (line.endswith('?') and 15 <= len(line) <= 300
            and not line.lower().startswith(('http', 'www')))


def extract_content(extract_dir, max_chars=20000, max_html_files=40):
    """Mine all human-readable content from an extracted package.

    Returns a dict with slide_titles, text, word_count, quiz_questions,
    captions, embedded metadata found in content, and the list of sources used.
    """
    extract_dir = Path(extract_dir)
    sources = set()
    texts = []
    questions = []
    slide_titles = []
    captions = {}
    embedded = {}

    # ── Articulate Storyline data files ──────────────────────────────────
    storyline_files = sorted(extract_dir.rglob('html5/data/js/*.js'),
                             key=lambda p: (p.name != 'data.js', str(p)))
    if not storyline_files:
        storyline_files = [p for p in extract_dir.rglob('*.js')
                           if p.name in ('data.js', 'frame.js')]
    for js in storyline_files[:200]:
        t, q, st = parse_storyline_js(js)
        if t or st:
            sources.add('storyline_data')
        texts.extend(t)
        questions.extend(q)
        slide_titles.extend(st)

    # ── Articulate Rise embedded JSON ────────────────────────────────────
    rise_index = extract_dir / 'scormcontent' / 'index.html'
    if not rise_index.exists():
        hits = list(extract_dir.rglob('scormcontent/index.html'))
        rise_index = hits[0] if hits else None
    if rise_index and rise_index.exists():
        t, q, lt, ct, cd = parse_rise_index(rise_index)
        if t or lt or ct:
            sources.add('rise_json')
        texts.extend(t)
        questions.extend(q)
        slide_titles.extend(lt)
        if ct:
            embedded['title'] = ct
        if cd:
            embedded['description'] = cd

    # ── Plain HTML files ─────────────────────────────────────────────────
    html_files = sorted([p for p in extract_dir.rglob('*.htm*')
                         if p.stat().st_size < 5_000_000],
                        key=lambda p: len(p.parts))
    for hf in html_files[:max_html_files]:
        text, headings, page_title = extract_html_content(hf)
        if text and len(text) > 40:
            sources.add('html')
            texts.append(text)
        slide_titles.extend(headings)
        if page_title and 'title' not in embedded and page_title.lower() not in (
                'index', 'untitled', 'blank'):
            embedded.setdefault('page_title', page_title)

    # ── Sentence-like strings in hand-written JS (SPA courses) ──────────
    storyline_set = {str(p) for p in storyline_files}
    for js in sorted(extract_dir.rglob('*.js'), key=lambda p: len(p.parts))[:40]:
        if str(js) in storyline_set:
            continue
        found = parse_js_strings(js)
        if found:
            sources.add('js_strings')
            texts.extend(found)

    # ── Captions / subtitles ─────────────────────────────────────────────
    for ext in ('*.vtt', '*.srt'):
        for cf in sorted(extract_dir.rglob(ext))[:20]:
            text = parse_caption_file(cf)
            if text:
                sources.add('captions')
                captions[str(cf.relative_to(extract_dir))] = text[:5000]
                texts.append(text)

    # ── Generic JSON content files (Evolve/Adapt/Gomo etc.) ─────────────
    for jf in sorted(extract_dir.rglob('*.json'), key=lambda p: len(p.parts))[:60]:
        if jf.stat().st_size > 3_000_000:
            continue
        try:
            data = json.loads(jf.read_text(encoding='utf-8', errors='ignore'))
        except (json.JSONDecodeError, OSError):
            continue
        before = len(texts)
        _harvest_json(data, texts, questions)
        if len(texts) > before:
            sources.add('json_content')

    # ── Consolidate ──────────────────────────────────────────────────────
    seen = set()
    unique_texts = []
    for t in texts:
        key = t[:120]
        if key not in seen:
            seen.add(key)
            unique_texts.append(t)

    full_text = '\n'.join(unique_texts)
    # Heuristic question detection from text bodies
    for line in re.split(r'(?<=[.?!])\s+', full_text):
        if _looks_like_question(line):
            questions.append(line.strip())

    questions = list(dict.fromkeys(q.strip() for q in questions if q.strip()))[:50]
    slide_titles = list(dict.fromkeys(s for s in slide_titles if s and len(s) < 200))[:100]
    word_count = len(full_text.split())

    return {
        'sources': sorted(sources),
        'slide_titles': slide_titles,
        'text': full_text[:max_chars],
        'word_count': word_count,
        'quiz_questions': questions,
        'captions': captions,
        'embedded_metadata': embedded,
        'has_assessment': bool(questions),
    }
