"""Package-level operations: safe extraction, standard detection
(SCORM / xAPI / cmi5 / AICC), authoring-tool fingerprinting, file
inventory and launch-point discovery. All deterministic."""

import hashlib
import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .manifest import strip_ns, _first_langstring


def safe_extract(zip_path, dest_dir):
    """Extract a zip with zip-slip protection. Returns list of extracted names."""
    dest = Path(dest_dir).resolve()
    extracted = []
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for info in zf.infolist():
            name = info.filename
            # Reject absolute paths and traversal
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest) + os.sep) and target != dest:
                continue
            zf.extract(info, str(dest))
            extracted.append(name)
    return extracted


def file_sha256(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(block)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def find_file(extract_dir, filename):
    """Find a file by exact lowercase name anywhere in the tree (shallowest wins)."""
    matches = []
    for root_dir, _dirs, files in os.walk(extract_dir):
        for fname in files:
            if fname.lower() == filename.lower():
                matches.append(Path(root_dir) / fname)
    if not matches:
        return None
    return min(matches, key=lambda p: len(p.parts))


def detect_package_type(extract_dir):
    """Identify the e-learning standard of the extracted package.

    Returns dict: {'type': 'scorm'|'xapi'|'cmi5'|'aicc'|'unknown',
                   'descriptor': path-or-None}
    """
    extract_dir = Path(extract_dir)
    manifest = find_file(extract_dir, 'imsmanifest.xml')
    if manifest:
        return {'type': 'scorm', 'descriptor': str(manifest)}
    cmi5 = find_file(extract_dir, 'cmi5.xml')
    if cmi5:
        return {'type': 'cmi5', 'descriptor': str(cmi5)}
    tincan = find_file(extract_dir, 'tincan.xml')
    if tincan:
        return {'type': 'xapi', 'descriptor': str(tincan)}
    crs = list(extract_dir.rglob('*.crs')) + list(extract_dir.rglob('*.CRS'))
    if crs:
        return {'type': 'aicc', 'descriptor': str(crs[0])}
    return {'type': 'unknown', 'descriptor': None}


def parse_tincan(tincan_path):
    """Parse an xAPI tincan.xml: activity id, name, description, launch href."""
    out = {'title': '', 'description': '', 'launch': '', 'activity_id': ''}
    try:
        root = ET.parse(str(tincan_path)).getroot()
    except ET.ParseError:
        return out
    for act in root.iter():
        if strip_ns(act.tag).lower() != 'activity':
            continue
        out['activity_id'] = act.get('id', '')
        for child in act:
            tag = strip_ns(child.tag).lower()
            if tag == 'name' and not out['title']:
                out['title'] = _first_langstring(child)
            elif tag == 'description' and not out['description']:
                out['description'] = _first_langstring(child)
            elif tag == 'launch' and not out['launch']:
                out['launch'] = (child.text or '').strip()
        if out['title'] or out['launch']:
            break
    return out


def parse_cmi5(cmi5_path):
    """Parse a cmi5.xml courseStructure: course id/title/description, first AU launch."""
    out = {'title': '', 'description': '', 'launch': '', 'course_id': ''}
    try:
        root = ET.parse(str(cmi5_path)).getroot()
    except ET.ParseError:
        return out
    for elem in root.iter():
        tag = strip_ns(elem.tag).lower()
        if tag == 'course' and not out['course_id']:
            out['course_id'] = elem.get('id', '')
            for child in elem:
                ctag = strip_ns(child.tag).lower()
                if ctag == 'title':
                    out['title'] = _first_langstring(child)
                elif ctag == 'description':
                    out['description'] = _first_langstring(child)
        elif tag == 'au' and not out['launch']:
            for child in elem:
                if strip_ns(child.tag).lower() == 'url' and child.text:
                    out['launch'] = child.text.strip()
    return out


def parse_aicc(crs_path):
    """Parse AICC .crs (INI-style) + sibling .au for title/description/launch."""
    out = {'title': '', 'description': '', 'launch': ''}
    crs_path = Path(crs_path)
    try:
        text = crs_path.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return out
    m = re.search(r'^\s*Course_Title\s*=\s*(.+)$', text, re.MULTILINE | re.IGNORECASE)
    if m:
        out['title'] = m.group(1).strip()
    m = re.search(r'\[Course_Description\]\s*\n(.+?)(?:\n\[|\Z)', text,
                  re.DOTALL | re.IGNORECASE)
    if m:
        out['description'] = m.group(1).strip()
    # .au file: File_Name column holds the launch URL
    for au in crs_path.parent.glob('*.[aA][uU]'):
        try:
            lines = [l for l in au.read_text(encoding='utf-8', errors='ignore').splitlines()
                     if l.strip()]
        except OSError:
            continue
        if len(lines) >= 2:
            header = [h.strip().strip('"').lower() for h in lines[0].split(',')]
            row = [c.strip().strip('"') for c in lines[1].split(',')]
            if 'file_name' in header:
                idx = header.index('file_name')
                if idx < len(row):
                    out['launch'] = row[idx]
        break
    return out


# ── Authoring tool fingerprints ──────────────────────────────────────────────

_TOOL_FILE_FINGERPRINTS = [
    # (tool name, list of path fragments — any match wins)
    ('Articulate Rise', ['scormcontent/index.html', 'scormdriver/indexapi.html']),
    ('Articulate Storyline', ['story.html', 'story_html5.html', 'html5/data/js/data.js',
                              'mobile/player.html', 'story_content/']),
    ('Adobe Captivate', ['assets/js/cpm.js', 'dr/cpm.js', 'project.txt', 'assets/cpquizinfo.xml']),
    ('iSpring', ['data/swfobject.js', 'res/data.js', 'data/player.js']),
    ('Lectora', ['trivantis.css', 'trivantis-azure.js', 'lectora.js']),
    ('Camtasia', ['scripts/config_xml.js', 'skins/express_show/']),
    ('Evolve', ['course/en/course.json', 'adapt/js/adapt.min.js']),
    ('Adapt Learning', ['adapt/js/adapt.min.js', 'course/config.json']),
    ('Gomo', ['gomo.js', 'wrapper/gomo_storage.js']),
    ('Elucidat', ['elucidat.js', 'js/elucidat']),
    ('Easygenerator', ['settings.js', 'easygenerator']),
    ('dominKnow', ['dominknow', 'dkpackage']),
]

_TOOL_CONTENT_FINGERPRINTS = [
    ('Articulate Rise', re.compile(r'articulate\s*rise|\brise\s*360\b', re.I)),
    ('Articulate Storyline', re.compile(r'articulate\s*storyline|storyline\s*360', re.I)),
    ('Adobe Captivate', re.compile(r'adobe\s*captivate', re.I)),
    ('iSpring', re.compile(r'\bispring\b', re.I)),
    ('Lectora', re.compile(r'\blectora\b|\btrivantis\b', re.I)),
    ('Camtasia', re.compile(r'\bcamtasia\b|\btechsmith\b', re.I)),
    ('Elucidat', re.compile(r'\belucidat\b', re.I)),
    ('Easygenerator', re.compile(r'\beasygenerator\b', re.I)),
    ('Gomo', re.compile(r'\bgomolearning\b|\bgomo learning\b', re.I)),
    ('dominKnow', re.compile(r'\bdominknow\b', re.I)),
    ('Adapt Learning', re.compile(r'\badapt learning\b|\badapt_framework\b', re.I)),
]


def detect_authoring_tool(extract_dir):
    """Fingerprint the authoring tool that produced the package.

    Returns {'name': str, 'confidence': 'high'|'medium'|'none', 'evidence': str}.
    """
    extract_dir = Path(extract_dir)
    all_paths = []
    for root_dir, _dirs, files in os.walk(extract_dir):
        rel_root = os.path.relpath(root_dir, extract_dir)
        for fname in files:
            rel = os.path.join(rel_root, fname).replace('\\', '/').lower()
            rel = rel[2:] if rel.startswith('./') else rel
            all_paths.append(rel)
        for d in _dirs:
            rel = os.path.join(rel_root, d).replace('\\', '/').lower()
            rel = rel[2:] if rel.startswith('./') else rel
            all_paths.append(rel + '/')

    joined = '\n'.join(all_paths)
    for tool, fragments in _TOOL_FILE_FINGERPRINTS:
        for frag in fragments:
            if frag in joined:
                return {'name': tool, 'confidence': 'high',
                        'evidence': f'file path: {frag}'}

    # Content sniffing: scan entry-ish HTML/JS files for tool strings
    candidates = []
    for pattern in ('*.html', '*.htm', '*.js'):
        candidates.extend(sorted(extract_dir.rglob(pattern))[:8])
    for path in candidates[:16]:
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')[:50000]
        except OSError:
            continue
        for tool, rx in _TOOL_CONTENT_FINGERPRINTS:
            if rx.search(text):
                return {'name': tool, 'confidence': 'medium',
                        'evidence': f'string match in {path.name}'}

    return {'name': 'unknown', 'confidence': 'none', 'evidence': ''}


MEDIA_EXTS = {
    'video': {'.mp4', '.webm', '.m4v', '.mov', '.flv'},
    'audio': {'.mp3', '.wav', '.ogg', '.m4a', '.aac'},
    'image': {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp'},
    'caption': {'.vtt', '.srt'},
    'document': {'.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx'},
}


def build_inventory(extract_dir):
    """File inventory: counts/sizes by type, media file lists, totals."""
    extract_dir = Path(extract_dir)
    by_extension = {}
    media = {'videos': [], 'audios': [], 'images_count': 0,
             'captions': [], 'documents': []}
    total_files = 0
    total_bytes = 0

    for root_dir, _dirs, files in os.walk(extract_dir):
        for fname in files:
            path = Path(root_dir) / fname
            rel = str(path.relative_to(extract_dir))
            ext = path.suffix.lower()
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            total_files += 1
            total_bytes += size
            entry = by_extension.setdefault(ext or '(none)', {'count': 0, 'bytes': 0})
            entry['count'] += 1
            entry['bytes'] += size

            if ext in MEDIA_EXTS['video']:
                media['videos'].append({'path': rel, 'bytes': size})
            elif ext in MEDIA_EXTS['audio']:
                media['audios'].append({'path': rel, 'bytes': size})
            elif ext in MEDIA_EXTS['image']:
                media['images_count'] += 1
            elif ext in MEDIA_EXTS['caption']:
                media['captions'].append(rel)
            elif ext in MEDIA_EXTS['document']:
                media['documents'].append(rel)

    media['videos'].sort(key=lambda v: -v['bytes'])
    media['audios'].sort(key=lambda v: -v['bytes'])

    return {
        'total_files': total_files,
        'total_uncompressed_bytes': total_bytes,
        'by_extension': dict(sorted(by_extension.items(),
                                    key=lambda kv: -kv[1]['bytes'])),
        'media': media,
        'has_video': bool(media['videos']),
        'has_audio': bool(media['audios']),
        'has_captions': bool(media['captions']),
    }


def find_entry_point(extract_dir, resources=None, package_type=None):
    """Find the launchable HTML entry point.

    Priority: manifest SCO href > xAPI/cmi5/AICC launch > known filenames >
    any root index.html > shallowest index*.html anywhere.
    Returns href relative to extract_dir, or None.
    """
    extract_dir = Path(extract_dir)

    def _exists(href):
        if not href:
            return False
        clean = href.split('?', 1)[0].split('#', 1)[0]
        return (extract_dir / clean).exists()

    # 1. SCO resource from manifest (SCO before asset)
    if resources:
        ranked = sorted(resources,
                        key=lambda r: 0 if 'sco' in (r.get('scorm_type') or '').lower() else 1)
        for res in ranked:
            href = res.get('href', '')
            if href and href.split('?')[0].lower().endswith(('.html', '.htm')) and _exists(href):
                return href

    # 2. Standard-specific descriptors
    pt = package_type or detect_package_type(extract_dir)
    if pt['type'] == 'xapi' and pt['descriptor']:
        launch = parse_tincan(pt['descriptor']).get('launch')
        if _exists(launch):
            return launch
    elif pt['type'] == 'cmi5' and pt['descriptor']:
        launch = parse_cmi5(pt['descriptor']).get('launch')
        if _exists(launch):
            return launch
    elif pt['type'] == 'aicc' and pt['descriptor']:
        launch = parse_aicc(pt['descriptor']).get('launch')
        if _exists(launch):
            return launch

    # 3. Known authoring-tool entry filenames
    for candidate in ['index_lms_html5.html', 'index_lms.html', 'indexAPI.html',
                      'scormdriver/indexAPI.html', 'story_html5.html', 'story.html',
                      'launcher.html', 'launch.html', 'default.htm', 'index.html']:
        if (extract_dir / candidate).exists():
            return candidate

    # 4. Shallowest index*.html anywhere
    matches = sorted(extract_dir.rglob('index*.htm*'), key=lambda p: len(p.parts))
    if matches:
        return str(matches[0].relative_to(extract_dir))

    # 5. Any html at root
    for f in sorted(extract_dir.glob('*.htm*')):
        return f.name

    return None
