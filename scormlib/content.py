"""Deterministic content mining — extracts human-readable course content
(slide titles, body text, quiz questions, transcripts) from a SCORM package
WITHOUT any LLM or network calls.

Understands: plain HTML SCOs, Articulate Storyline data files
(globalProvideData JS blobs), Articulate Rise embedded course JSON,
caption files (.vtt/.srt), and generic JSON content files."""

import base64
import html as html_mod
import json
import re
import zlib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

# Keys whose string values we harvest from authoring-tool JSON blobs
_TEXT_KEYS = {'title', 'text', 'alttext', 'caption', 'description', 'heading',
              'paragraph', 'label', 'slidetitle', 'displaytext', 'name',
              'transcript', 'notes', 'lmstext'}
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

_JS_ESCAPES = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f',
               "'": "'", '"': '"', '\\': '\\', '/': '/'}


def _decode_js_string(payload):
    """Decode a single-quoted JS string body without mangling UTF-8.

    (codecs unicode_escape corrupts non-ASCII text, so do it by hand.)"""
    out = []
    i = 0
    n = len(payload)
    while i < n:
        ch = payload[i]
        if ch != '\\' or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = payload[i + 1]
        if nxt in _JS_ESCAPES:
            out.append(_JS_ESCAPES[nxt])
            i += 2
        elif nxt == 'u' and i + 5 < n:
            try:
                out.append(chr(int(payload[i + 2:i + 6], 16)))
                i += 6
            except ValueError:
                out.append(nxt)
                i += 2
        elif nxt == 'x' and i + 3 < n:
            try:
                out.append(chr(int(payload[i + 2:i + 4], 16)))
                i += 4
            except ValueError:
                out.append(nxt)
                i += 2
        else:
            out.append(nxt)
            i += 2
    return ''.join(out)


def _strip_html(text):
    text = re.sub(r'<[^>]+>', ' ', text or '')
    return re.sub(r'\s+', ' ', html_mod.unescape(text)).strip()


def _extract_choice_quizzes(node, quiz_items, depth=0):
    """Find Storyline-style quiz interactions: {'lmstext': q, 'choices': [...]}.

    Also matches Rise-style: {'title': q, 'answers': [{'title':…,'correct':…}]}."""
    if depth > 40:
        return
    if isinstance(node, dict):
        choices = node.get('choices')
        if isinstance(choices, list) and node.get('lmstext'):
            options = [_strip_html(c.get('lmstext', '')) for c in choices
                       if isinstance(c, dict)]
            options = [o for o in options if o]
            if options:
                quiz_items.append({
                    'question': _strip_html(node['lmstext']),
                    'options': options,
                    'correct': [],
                })
        answers = node.get('answers')
        if isinstance(answers, list) and answers and \
                all(isinstance(a, dict) for a in answers) and \
                any('correct' in a for a in answers):
            options = [_strip_html(a.get('title', '')) for a in answers]
            options = [o for o in options if o]
            if options:
                quiz_items.append({
                    'question': _strip_html(node.get('title', '')),
                    'options': options,
                    'correct': [_strip_html(a.get('title', '')) for a in answers
                                if a.get('correct')],
                })
        for value in node.values():
            _extract_choice_quizzes(value, quiz_items, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _extract_choice_quizzes(item, quiz_items, depth + 1)


def _sum_timeline_durations_ms(node, depth=0):
    """Sum Storyline slide timeline durations (milliseconds)."""
    total = 0
    if depth > 40:
        return total
    if isinstance(node, dict):
        timeline = node.get('timeline')
        if isinstance(timeline, dict) and isinstance(
                timeline.get('duration'), (int, float)):
            total += timeline['duration']
        for value in node.values():
            total += _sum_timeline_durations_ms(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            total += _sum_timeline_durations_ms(item, depth + 1)
    return total


def parse_storyline_js(path):
    """Parse an Articulate Storyline data/js file (globalProvideData wrapper).

    Returns (texts, questions, slide_titles, quiz_items, duration_ms)."""
    texts, questions, slide_titles, quiz_items = [], [], [], []
    duration_ms = 0
    try:
        raw = Path(path).read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return texts, questions, slide_titles, quiz_items, duration_ms
    for kind, payload in _GLOBAL_PROVIDE_RE.findall(raw):
        unescaped = _decode_js_string(payload)
        try:
            data = json.loads(unescaped)
        except json.JSONDecodeError:
            continue
        if kind == 'caption' and isinstance(data, dict) and data.get('data'):
            # Storyline captions: percent-encoded WebVTT inside {"data": "..."}
            vtt = unquote(data['data'])
            lines = [re.sub(r'<[^>]+>', '', l).strip() for l in vtt.splitlines()
                     if l.strip() and '-->' not in l and l.strip() != 'WEBVTT'
                     and not l.strip().isdigit()]
            if lines:
                texts.append(' '.join(lines))
            continue
        if isinstance(data, dict):
            title = data.get('title')
            if isinstance(title, str) and title.strip() and kind in ('slide', 'data'):
                cleaned = _strip_html(title)
                if cleaned and cleaned.lower() != 'untitled slide':
                    slide_titles.append(cleaned)
            if kind == 'slide':
                duration_ms += _sum_timeline_durations_ms(data)
        _extract_choice_quizzes(data, quiz_items)
        _harvest_json(data, texts, questions)
    return texts, questions, slide_titles, quiz_items, duration_ms


def parse_storyline_meta(extract_dir):
    """Parse Storyline's root meta.xml: project title, human duration, slide count."""
    out = {}
    meta = Path(extract_dir) / 'meta.xml'
    if not meta.exists():
        return out
    try:
        root = ET.parse(str(meta)).getroot()
    except ET.ParseError:
        return out
    for elem in root.iter():
        tag = elem.tag.split('}')[-1].lower()
        if tag == 'project':
            if elem.get('title'):
                out['title'] = elem.get('title')
            if elem.get('duration'):
                out['duration_text'] = elem.get('duration')
        elif tag == 'application' and elem.get('name'):
            out['application'] = elem.get('name')
        elif tag == 'slidemeta' and elem.get('viewslides'):
            out['slide_count'] = elem.get('viewslides')
    return out


def parse_captivate(extract_dir):
    """Adobe Captivate: project.txt (JSON) + CPM.js accessibility strings.

    Returns (texts, slide_titles, quiz_items, meta) where meta may include
    title and duration_minutes (durationInFrames / frameRate)."""
    extract_dir = Path(extract_dir)
    texts, slide_titles, quiz_items = [], [], []
    meta = {}

    project = extract_dir / 'project.txt'
    if project.exists():
        try:
            data = json.loads(project.read_text(encoding='utf-8', errors='ignore'))
        except (json.JSONDecodeError, OSError):
            data = None
        if isinstance(data, dict) and \
                (data.get('metadata') or {}).get('generator') == 'Captivate':
            md = data['metadata']
            title = (md.get('title') or '').strip()
            if title and title.lower() not in ('', 'project', 'project description'):
                meta['title'] = title
            frames = md.get('durationInFrames')
            rate = md.get('frameRate')
            if isinstance(frames, (int, float)) and isinstance(rate, (int, float)) \
                    and rate > 0:
                meta['duration_minutes'] = round(frames / rate / 60, 2)
            for entry in data.get('toc') or []:
                if isinstance(entry, dict) and entry.get('title'):
                    slide_titles.append(entry['title'])
            for node in data.get('contentStructure') or []:
                if not isinstance(node, dict):
                    continue
                question = ((node.get('roles') or {}).get('question') or {})
                qtext = _strip_html(question.get('text', ''))
                if qtext:
                    quiz_items.append({'question': qtext, 'options': [],
                                       'correct': []})

    # Visible text lives in CPM.js accstr:'...' fields (minified JS object)
    for cpm in list(extract_dir.rglob('CPM.js'))[:3]:
        try:
            raw = cpm.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for m in re.finditer(r"accstr:'((?:[^'\\]|\\.)*)'", raw):
            s = _strip_html(_decode_js_string(m.group(1)))
            # filter auto-generated shape names ("Rectangle 12", filenames)
            if len(s) >= 15 and s.count(' ') >= 2 and \
                    not re.match(r'^(rectangle|oval|shape|image|smartshape|button)\b',
                                 s, re.I):
                texts.append(s)

    return texts, slide_titles, quiz_items, meta


_PRESINFO_RE = re.compile(r'presInfo\s*=\s*"([A-Za-z0-9+/=]+)"')
_QUIZINFO_RE = re.compile(r'quizInfo\s*=\s*"([A-Za-z0-9+/=]+)"')


def parse_ispring(extract_dir):
    """iSpring: presInfo (base64+zlib JSON in index.html) and quiz data blobs.

    Returns (texts, slide_titles, quiz_items, meta)."""
    extract_dir = Path(extract_dir)
    texts, slide_titles, quiz_items = [], [], []
    meta = {}

    for index in sorted(extract_dir.glob('*.htm*'))[:5]:
        try:
            raw = index.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        m = _PRESINFO_RE.search(raw)
        if not m:
            continue
        try:
            blob = base64.b64decode(m.group(1))
            data = json.loads(zlib.decompress(blob).decode('utf-8', errors='ignore'))
        except Exception:
            continue
        if isinstance(data.get('t'), str) and data['t'].strip():
            meta['title'] = data['t'].strip()
        for slide in data.get('s') or []:
            if not isinstance(slide, dict):
                continue
            if isinstance(slide.get('t'), str) and slide['t'].strip():
                slide_titles.append(slide['t'].strip())
            # 'x' holds the slide's extracted plain text, \r\n separated
            if isinstance(slide.get('x'), str) and slide['x'].strip():
                texts.append(re.sub(r'\s+', ' ', slide['x']).strip())
        break

    for quiz_js in sorted(extract_dir.rglob('data/quiz*.js'))[:20]:
        try:
            raw = quiz_js.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        m = _QUIZINFO_RE.search(raw)
        if not m:
            continue
        try:
            data = json.loads(base64.b64decode(m.group(1)).decode(
                'utf-8', errors='ignore'))
        except Exception:
            continue
        # questions: d.sl.g[].S[] with D.h (HTML) / D.a (plain) prompts
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                d = node.get('D')
                if isinstance(d, dict) and (d.get('a') or d.get('h')):
                    q = d.get('a') if isinstance(d.get('a'), str) else ''
                    if not q:
                        q = _strip_html(d.get('h', ''))
                    if q and len(q) > 8:
                        quiz_items.append({'question': q.strip(), 'options': [],
                                           'correct': []})
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)

    return texts, slide_titles, quiz_items, meta


def parse_camtasia_config(extract_dir):
    """Camtasia *_config.xml: dc:title + xmpDM:duration (milliseconds)."""
    meta = {}
    for cfg in sorted(Path(extract_dir).rglob('*_config.xml'))[:3]:
        try:
            raw = cfg.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if 'techsmith' not in raw.lower():
            continue
        m = re.search(r'<dc:title>\s*(?:<[^>]+>)*([^<]+)', raw)
        if m and m.group(1).strip():
            meta['title'] = m.group(1).strip()
        m = re.search(r'xmpDM:duration[^>]*xmpDM:value="(\d+)"', raw) or \
            re.search(r'xmpDM:value="(\d+)"[^>]*xmpDM:duration', raw)
        if m:
            meta['duration_minutes'] = round(int(m.group(1)) / 1000 / 60, 2)
        if meta:
            break
    return meta


_RISE_B64_RE = re.compile(
    r'(?:courseData|window\.courseData)\s*=\s*["\']([A-Za-z0-9+/=\s]{100,})["\']')
# Other Rise builds: __resolveJsonp("course:und","<b64>") or deserialize("<b64>")
_RISE_JSONP_RE = re.compile(
    r'__resolveJsonp\(\s*["\']course:[^"\']*["\']\s*,\s*["\']([A-Za-z0-9+/=\s]{100,})["\']')
_RISE_DESER_RE = re.compile(r'deserialize\(\s*["\']([A-Za-z0-9+/=\s]{100,})["\']')


def parse_rise_index(path, extract_dir=None):
    """Parse an Articulate Rise export for the embedded course JSON.

    Checks all three known encodings: window.courseData base64 in index.html,
    __resolveJsonp in locales/und.js, and inline deserialize("<b64>").

    Returns (texts, questions, lesson_titles, quiz_items, course_title,
    course_description)."""
    texts, questions, lesson_titles, quiz_items = [], [], [], []
    course_title, course_description = '', ''

    sources = [Path(path)]
    if extract_dir:
        sources += sorted(Path(extract_dir).rglob('locales/*.js'))[:5]

    payloads = []
    for src in sources:
        try:
            raw = src.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for rx in (_RISE_B64_RE, _RISE_JSONP_RE, _RISE_DESER_RE):
            m = rx.search(raw)
            if m:
                try:
                    payloads.append(base64.b64decode(
                        re.sub(r'\s+', '', m.group(1))).decode(
                        'utf-8', errors='ignore'))
                except Exception:
                    pass
        # Inline JSON assignments (Rise also ships plain JSON in some versions)
        for jsm in re.finditer(r'=\s*(\{"(?:course|share|lessons)".{200,}?\});?\s*\n',
                               raw):
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
                course_description = _strip_html(course['description'])
            for lesson in course.get('lessons', []) or []:
                if isinstance(lesson, dict) and isinstance(lesson.get('title'), str):
                    if lesson.get('type') != 'section':  # sections = group headers
                        lesson_titles.append(lesson['title'].strip())
        _extract_choice_quizzes(data, quiz_items)
        _harvest_json(data, texts, questions)

    return texts, questions, lesson_titles, quiz_items, course_title, course_description


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
    quiz_items = []
    captions = {}
    embedded = {}
    estimated_duration_ms = 0

    # ── Articulate Storyline data files (+ caption .js wrappers) ────────
    storyline_files = sorted(extract_dir.rglob('html5/data/js/*.js'),
                             key=lambda p: (p.name != 'data.js', str(p)))
    storyline_files += sorted(extract_dir.rglob('story_content/*_captions.js'))
    if not storyline_files:
        storyline_files = [p for p in extract_dir.rglob('*.js')
                           if p.name in ('data.js', 'frame.js')]
    for js in storyline_files[:300]:
        t, q, st, qi, dur_ms = parse_storyline_js(js)
        if t or st:
            sources.add('storyline_data')
        texts.extend(t)
        questions.extend(q)
        slide_titles.extend(st)
        quiz_items.extend(qi)
        estimated_duration_ms += dur_ms

    sl_meta = parse_storyline_meta(extract_dir)
    if sl_meta:
        sources.add('storyline_meta')
        if sl_meta.get('title'):
            embedded.setdefault('title', sl_meta['title'])
        if sl_meta.get('duration_text'):
            embedded['duration_text'] = sl_meta['duration_text']

    # ── Articulate Rise embedded JSON ────────────────────────────────────
    rise_index = extract_dir / 'scormcontent' / 'index.html'
    if not rise_index.exists():
        hits = list(extract_dir.rglob('scormcontent/index.html'))
        rise_index = hits[0] if hits else None
    if rise_index and rise_index.exists():
        t, q, lt, qi, ct, cd = parse_rise_index(rise_index, extract_dir)
        if t or lt or ct:
            sources.add('rise_json')
        texts.extend(t)
        questions.extend(q)
        slide_titles.extend(lt)
        quiz_items.extend(qi)
        if ct:
            embedded['title'] = ct
        if cd:
            embedded['description'] = cd

    # ── Adobe Captivate (project.txt + CPM.js) ───────────────────────────
    t, st, qi, cap_meta = parse_captivate(extract_dir)
    if t or st or qi or cap_meta:
        sources.add('captivate_data')
        texts.extend(t)
        slide_titles.extend(st)
        quiz_items.extend(qi)
        if cap_meta.get('title'):
            embedded.setdefault('title', cap_meta['title'])
        if cap_meta.get('duration_minutes'):
            embedded['duration_minutes'] = cap_meta['duration_minutes']

    # ── iSpring (presInfo / quizInfo blobs) ──────────────────────────────
    t, st, qi, isp_meta = parse_ispring(extract_dir)
    if t or st or qi or isp_meta:
        sources.add('ispring_data')
        texts.extend(t)
        slide_titles.extend(st)
        quiz_items.extend(qi)
        if isp_meta.get('title'):
            embedded.setdefault('title', isp_meta['title'])

    # ── Camtasia (config XML: title + video duration) ────────────────────
    cam_meta = parse_camtasia_config(extract_dir)
    if cam_meta:
        sources.add('camtasia_config')
        if cam_meta.get('title'):
            embedded.setdefault('title', cam_meta['title'])
        if cam_meta.get('duration_minutes'):
            embedded.setdefault('duration_minutes', cam_meta['duration_minutes'])

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

    # Structured quiz items take precedence in the question list
    questions = [qi['question'] for qi in quiz_items if qi.get('question')] + questions
    questions = list(dict.fromkeys(q.strip() for q in questions if q.strip()))[:50]
    slide_titles = list(dict.fromkeys(s for s in slide_titles if s and len(s) < 200))[:100]
    word_count = len(full_text.split())

    # De-duplicate quiz items by question text
    seen_q = set()
    unique_items = []
    for qi in quiz_items:
        key = qi.get('question', '')[:120]
        if key and key not in seen_q:
            seen_q.add(key)
            unique_items.append(qi)

    estimated_duration = embedded.get('duration_minutes')
    if not estimated_duration and estimated_duration_ms > 30000:
        estimated_duration = round(estimated_duration_ms / 60000, 2)

    return {
        'sources': sorted(sources),
        'slide_titles': slide_titles,
        'text': full_text[:max_chars],
        'word_count': word_count,
        'quiz_questions': questions,
        'quiz_items': unique_items[:50],
        'captions': captions,
        'embedded_metadata': embedded,
        'estimated_duration_minutes': estimated_duration,
        'has_assessment': bool(questions),
    }
