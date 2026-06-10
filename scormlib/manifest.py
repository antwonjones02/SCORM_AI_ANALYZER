"""SCORM manifest (imsmanifest.xml) deep parser.

Handles SCORM 1.2 and SCORM 2004 (all editions), inline IEEE LOM metadata,
external metadata files referenced via <adlcp:location>, organizations,
items (with objectives, mastery scores, prerequisites), and resources.

Everything here is deterministic — no network, no LLM.
"""

import re
from pathlib import Path
from xml.etree import ElementTree as ET


def strip_ns(tag):
    """Strip XML namespace from a tag or attribute name."""
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


def _local_attr(elem, name):
    """Get an attribute by local name regardless of namespace."""
    for k, v in elem.attrib.items():
        if strip_ns(k).lower() == name.lower():
            return v
    return None


def _first_langstring(elem):
    """Get text from a LOM element that may wrap text in <langstring>/<string>."""
    if elem is None:
        return ''
    text = (elem.text or '').strip()
    if text:
        return text
    for child in elem.iter():
        tag = strip_ns(child.tag).lower()
        if tag in ('langstring', 'string') and child.text and child.text.strip():
            return child.text.strip()
    return ''


def _find_children(elem, local_name):
    """Direct children matching a local tag name (case-insensitive)."""
    return [c for c in elem if strip_ns(c.tag).lower() == local_name.lower()]


def _find_descendants(elem, local_name):
    """All descendants matching a local tag name (case-insensitive)."""
    name = local_name.lower()
    return [c for c in elem.iter() if strip_ns(c.tag).lower() == name]


def parse_duration_to_minutes(raw):
    """Normalize a duration string to minutes (float) or None.

    Supports ISO 8601 (PT1H30M15S, P1DT2H), SCORM 1.2 timespans
    (0000:05:30.00), and plain hh:mm:ss strings.
    """
    if not raw:
        return None
    raw = raw.strip()

    # ISO 8601: P[nD]T[nH][nM][nS]
    m = re.match(
        r'^P(?:(?P<days>[\d.]+)D)?'
        r'(?:T(?:(?P<hours>[\d.]+)H)?(?:(?P<mins>[\d.]+)M)?(?:(?P<secs>[\d.]+)S)?)?$',
        raw, re.IGNORECASE)
    if m and any(m.groupdict().values()):
        days = float(m.group('days') or 0)
        hours = float(m.group('hours') or 0)
        mins = float(m.group('mins') or 0)
        secs = float(m.group('secs') or 0)
        total = days * 24 * 60 + hours * 60 + mins + secs / 60
        return round(total, 2) if total else None

    # hh:mm:ss(.ss) — including SCORM 1.2 "0000:05:30.00"
    m = re.match(r'^(\d{1,4}):(\d{1,2}):(\d{1,2}(?:\.\d+)?)$', raw)
    if m:
        total = int(m.group(1)) * 60 + int(m.group(2)) + float(m.group(3)) / 60
        return round(total, 2) if total else None

    # bare minutes ("45", "45 min")
    m = re.match(r'^(\d+(?:\.\d+)?)\s*(?:m|min|mins|minutes)?$', raw, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        return round(val, 2) if val else None

    return None


def detect_scorm_version(root):
    """Detect SCORM version from manifest namespace or <schemaversion>."""
    ns = root.tag
    version = None
    if 'imscp_rootv1p1p2' in ns or 'imsproject' in ns:
        version = '1.2'
    elif 'imscp_v1p1' in ns or 'imsglobal' in ns:
        version = '2004'

    # <schemaversion> gives the most specific answer (e.g. "2004 3rd Edition")
    for child in root.iter():
        if strip_ns(child.tag).lower() == 'schemaversion':
            text = (child.text or '').strip()
            if text:
                if text == '1.2':
                    return '1.2'
                if '2004' in text or text.startswith('1.3') or 'CAM' in text:
                    return f'2004 ({text})' if '2004' in text and 'Edition' in text else '2004'
    return version or 'unknown'


def _parse_vcard_name(text):
    """Extract a display name from a vCard blob (FN: line) or return text as-is."""
    if not text:
        return ''
    m = re.search(r'^FN[;:](.+)$', text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    # Strip vCard wrapper noise if present
    if 'BEGIN:VCARD' in text.upper():
        lines = [l for l in text.splitlines()
                 if l.strip() and not re.match(r'^(BEGIN|END|VERSION)[;:]', l.strip(), re.I)]
        for l in lines:
            if ':' in l:
                return l.split(':', 1)[1].strip()
        return ''
    return text.strip()


def extract_lom_metadata(meta_root):
    """Extract every useful field from a LOM metadata tree (1.2 imsmd or 2004 LOM)."""
    out = {}

    # ── general ──────────────────────────────────────────────────────────
    for gen in _find_descendants(meta_root, 'general'):
        for t in _find_children(gen, 'title'):
            val = _first_langstring(t)
            if val and not out.get('title'):
                out['title'] = val
        for d in _find_children(gen, 'description'):
            val = _first_langstring(d)
            if val and not out.get('description'):
                out['description'] = val
        for k in _find_children(gen, 'keyword'):
            val = _first_langstring(k)
            if val:
                out.setdefault('keywords', []).append(val)
        for lang in _find_children(gen, 'language'):
            if lang.text and lang.text.strip() and not out.get('language'):
                out['language'] = lang.text.strip()
        for ident in _find_children(gen, 'identifier'):
            entry = ''
            catalog = ''
            for c in ident.iter():
                tag = strip_ns(c.tag).lower()
                if tag == 'entry':
                    entry = _first_langstring(c) or (c.text or '').strip()
                elif tag in ('catalog', 'catalogue'):
                    catalog = (c.text or '').strip()
            if ident.text and ident.text.strip() and not entry:
                entry = ident.text.strip()
            if entry:
                out.setdefault('identifiers', []).append(
                    {'catalog': catalog, 'entry': entry})
        # 1.2 style: <catalogentry><catalog/><entry>
        for ce in _find_children(gen, 'catalogentry'):
            catalog = ''
            entry = ''
            for c in ce.iter():
                tag = strip_ns(c.tag).lower()
                if tag in ('catalog', 'catalogue'):
                    catalog = (c.text or '').strip()
                elif tag == 'entry':
                    entry = _first_langstring(c)
            if entry:
                out.setdefault('identifiers', []).append(
                    {'catalog': catalog, 'entry': entry})
        for agg in _find_children(gen, 'aggregationlevel'):
            val = _lom_vocab_value(agg)
            if val:
                out['aggregation_level'] = val

    # ── lifecycle ────────────────────────────────────────────────────────
    for lc in _find_descendants(meta_root, 'lifecycle'):
        for v in _find_children(lc, 'version'):
            val = _first_langstring(v)
            if val and not out.get('version'):
                out['version'] = val
        for s in _find_children(lc, 'status'):
            val = _lom_vocab_value(s)
            if val:
                out['status'] = val
        for contrib in _find_descendants(lc, 'contribute'):
            role = ''
            entities = []
            date = ''
            for c in contrib:
                tag = strip_ns(c.tag).lower()
                if tag == 'role':
                    role = _lom_vocab_value(c)
                elif tag in ('entity', 'centity'):
                    # entity text may sit directly, in a <langstring>, or in a
                    # nested <vcard> element — gather all descendant text
                    blob = ' '.join(t for t in c.itertext() if t.strip())
                    name = _parse_vcard_name(blob)
                    if name:
                        entities.append(name)
                elif tag == 'date':
                    date = _first_langstring(c) or _find_dt(c)
            if entities:
                out.setdefault('contributors', []).append(
                    {'role': role or 'unknown', 'entities': entities, 'date': date})
                if role.lower() in ('author', 'creator', 'content provider') and not out.get('author'):
                    out['author'] = entities[0]

    if not out.get('author') and out.get('contributors'):
        out['author'] = out['contributors'][0]['entities'][0]

    # ── technical ────────────────────────────────────────────────────────
    for tech in _find_descendants(meta_root, 'technical'):
        for d in _find_children(tech, 'duration'):
            val = _find_dt(d) or _first_langstring(d)
            if val and not out.get('technical_duration'):
                out['technical_duration'] = val
        for f in _find_children(tech, 'format'):
            if f.text and f.text.strip():
                out.setdefault('formats', []).append(f.text.strip())

    # ── educational ──────────────────────────────────────────────────────
    for edu in _find_descendants(meta_root, 'educational'):
        for tl in _find_children(edu, 'typicallearningtime'):
            val = _find_dt(tl) or _first_langstring(tl)
            if val and not out.get('typical_learning_time'):
                out['typical_learning_time'] = val
        for d in _find_children(edu, 'difficulty'):
            val = _lom_vocab_value(d)
            if val:
                out['difficulty'] = val
        for it in _find_children(edu, 'interactivitytype'):
            val = _lom_vocab_value(it)
            if val:
                out['interactivity_type'] = val
        for il in _find_children(edu, 'interactivitylevel'):
            val = _lom_vocab_value(il)
            if val:
                out['interactivity_level'] = val
        for lrt in _find_children(edu, 'learningresourcetype'):
            val = _lom_vocab_value(lrt)
            if val:
                out.setdefault('learning_resource_types', []).append(val)
        for role in _find_children(edu, 'intendedenduserrole'):
            val = _lom_vocab_value(role)
            if val:
                out.setdefault('intended_end_user_roles', []).append(val)
        for ctx in _find_children(edu, 'context'):
            val = _lom_vocab_value(ctx)
            if val:
                out.setdefault('educational_contexts', []).append(val)
        for ar in _find_children(edu, 'typicalagerange'):
            val = _first_langstring(ar)
            if val:
                out['typical_age_range'] = val
        for d in _find_children(edu, 'description'):
            val = _first_langstring(d)
            if val and not out.get('educational_description'):
                out['educational_description'] = val

    # ── rights ───────────────────────────────────────────────────────────
    for rights in _find_descendants(meta_root, 'rights'):
        cost = ''
        copyright_flag = ''
        desc = ''
        for c in rights:
            tag = strip_ns(c.tag).lower()
            if tag == 'cost':
                cost = _lom_vocab_value(c)
            elif tag == 'copyrightandotherrestrictions':
                copyright_flag = _lom_vocab_value(c)
            elif tag == 'description':
                desc = _first_langstring(c)
        text = desc or copyright_flag
        if text and not out.get('copyright'):
            out['copyright'] = text
        if cost:
            out['cost'] = cost

    # ── classification → categories / taxonomy ──────────────────────────
    for cls in _find_descendants(meta_root, 'classification'):
        purpose = ''
        for p in _find_children(cls, 'purpose'):
            purpose = _lom_vocab_value(p)
        for tp in _find_descendants(cls, 'taxonpath'):
            source = ''
            taxons = []
            for c in tp:
                tag = strip_ns(c.tag).lower()
                if tag == 'source':
                    source = _first_langstring(c)
                elif tag == 'taxon':
                    for e in c.iter():
                        if strip_ns(e.tag).lower() == 'entry':
                            val = _first_langstring(e)
                            if val:
                                taxons.append(val)
        # fall through: also catch keyword-style classification
            if taxons:
                out.setdefault('classifications', []).append({
                    'purpose': purpose, 'source': source, 'taxons': taxons})
        for kw in _find_children(cls, 'keyword'):
            val = _first_langstring(kw)
            if val:
                out.setdefault('classification_keywords', []).append(val)

    return out


def _lom_vocab_value(elem):
    """Extract the value from a LOM vocabulary element (<value><langstring>…)."""
    if elem is None:
        return ''
    for c in elem.iter():
        if strip_ns(c.tag).lower() == 'value':
            return _first_langstring(c) or (c.text or '').strip()
    return _first_langstring(elem)


def _find_dt(elem):
    """Find a <datetime>/<duration> text inside a LOM duration element."""
    for c in elem.iter():
        tag = strip_ns(c.tag).lower()
        if tag in ('datetime', 'duration') and c.text and c.text.strip():
            return c.text.strip()
    return ''


def _resolve_external_metadata(root, manifest_dir):
    """Find <adlcp:location> references and parse the external metadata files."""
    results = []
    for meta in _find_descendants(root, 'metadata'):
        for loc in _find_descendants(meta, 'location'):
            href = (loc.text or '').strip()
            if not href:
                continue
            candidate = (Path(manifest_dir) / href).resolve()
            try:
                candidate.relative_to(Path(manifest_dir).resolve())
            except ValueError:
                continue  # path escapes the package — ignore
            if candidate.exists() and candidate.suffix.lower() == '.xml':
                try:
                    ext_root = ET.parse(str(candidate)).getroot()
                    results.append(extract_lom_metadata(ext_root))
                except ET.ParseError:
                    pass
    return results


def extract_metadata(root, manifest_dir=None):
    """Extract full course metadata: inline LOM, external LOM files, fallbacks."""
    metadata = {
        'title': '',
        'description': '',
        'keywords': [],
        'version': '',
        'language': '',
        'duration': '',
        'duration_minutes': None,
        'copyright': '',
        'author': '',
        'identifier': root.get('identifier', ''),
        'package_version': root.get('version', ''),
    }

    # Top-level <metadata> blocks (inline LOM)
    lom = {}
    for meta in _find_children(root, 'metadata'):
        lom = extract_lom_metadata(meta)
        if lom:
            break

    # External metadata files via <adlcp:location>
    if manifest_dir:
        for ext in _resolve_external_metadata(root, manifest_dir):
            for k, v in ext.items():
                if k not in lom or not lom[k]:
                    lom[k] = v
                elif isinstance(lom.get(k), list) and isinstance(v, list):
                    lom[k] = lom[k] + [x for x in v if x not in lom[k]]

    for key in ('title', 'description', 'language', 'version', 'copyright', 'author'):
        if lom.get(key):
            metadata[key] = lom[key]
    if lom.get('keywords'):
        metadata['keywords'] = list(dict.fromkeys(lom['keywords']))

    # Duration: typicalLearningTime > technical duration
    raw_duration = lom.get('typical_learning_time') or lom.get('technical_duration') or ''
    metadata['duration'] = raw_duration
    metadata['duration_minutes'] = parse_duration_to_minutes(raw_duration)

    # Rich LOM extras (only included when present)
    for key in ('difficulty', 'interactivity_type', 'interactivity_level',
                'learning_resource_types', 'intended_end_user_roles',
                'educational_contexts', 'typical_age_range', 'status', 'cost',
                'aggregation_level', 'identifiers', 'contributors',
                'classifications', 'classification_keywords', 'formats',
                'educational_description', 'typical_learning_time'):
        if lom.get(key):
            metadata[key] = lom[key]

    # Categories: flatten classification taxons for easy consumption
    categories = []
    for cls in lom.get('classifications', []):
        categories.extend(cls.get('taxons', []))
    categories.extend(lom.get('classification_keywords', []))
    if categories:
        metadata['categories'] = list(dict.fromkeys(categories))

    # Fallback title: first organization title, then manifest identifier
    if not metadata['title']:
        for orgs in _find_descendants(root, 'organizations'):
            for org in _find_children(orgs, 'organization'):
                for t in _find_children(org, 'title'):
                    if t.text and t.text.strip():
                        metadata['title'] = t.text.strip()
                        break
                if metadata['title']:
                    break
            if metadata['title']:
                break
    if not metadata['title']:
        metadata['title'] = metadata['identifier']

    # schemaversion as version fallback
    if not metadata['version']:
        for child in root.iter():
            if strip_ns(child.tag).lower() == 'schemaversion' and child.text:
                metadata['version'] = child.text.strip()
                break

    return metadata


def parse_item(item_elem, depth=0):
    """Recursively parse an <item> element."""
    item = {
        'id': item_elem.get('identifier', ''),
        'resource_ref': item_elem.get('identifierref', ''),
        'title': '',
        'objectives': [],
        'time_limit': '',
        'mastery_score': '',
        'prerequisites': '',
        'parameters': item_elem.get('parameters', ''),
        'visible': item_elem.get('isvisible', 'true'),
        'children': [],
    }

    for child in item_elem:
        tag = strip_ns(child.tag).lower()
        if tag == 'title':
            item['title'] = (child.text or '').strip()
        elif tag == 'objectives':
            for obj in child.iter():
                obj_tag = strip_ns(obj.tag).lower()
                if obj_tag in ('objective', 'primaryobjective'):
                    obj_id = obj.get('objectiveID') or obj.get('objectiveId') or ''
                    desc = ''
                    min_measure = ''
                    for d in obj.iter():
                        dt = strip_ns(d.tag).lower()
                        if dt == 'description' and d.text:
                            desc = d.text.strip()
                        elif dt == 'minnormalizedmeasure' and d.text:
                            min_measure = d.text.strip()
                    entry = {'id': obj_id, 'description': desc}
                    if min_measure:
                        entry['min_normalized_measure'] = min_measure
                    item['objectives'].append(entry)
        elif tag == 'timelimitaction':
            item['time_limit'] = (child.text or '').strip()
        elif tag == 'masteryscore':
            item['mastery_score'] = (child.text or '').strip()
        elif tag == 'prerequisites':
            item['prerequisites'] = (child.text or '').strip()
        elif tag == 'sequencing':
            # SCORM 2004: objectives + rollup live under imsss:sequencing
            for obj in child.iter():
                obj_tag = strip_ns(obj.tag).lower()
                if obj_tag in ('objective', 'primaryobjective'):
                    obj_id = obj.get('objectiveID') or ''
                    min_measure = ''
                    for d in obj.iter():
                        if strip_ns(d.tag).lower() == 'minnormalizedmeasure' and d.text:
                            min_measure = d.text.strip()
                    entry = {'id': obj_id, 'description': ''}
                    if min_measure:
                        entry['min_normalized_measure'] = min_measure
                        if not item['mastery_score']:
                            try:
                                item['mastery_score'] = str(round(float(min_measure) * 100))
                            except ValueError:
                                pass
                    if obj_id or min_measure:
                        item['objectives'].append(entry)
        elif tag == 'item':
            item['children'].append(parse_item(child, depth + 1))

    return item


def extract_organizations(root):
    """Extract the course structure tree from <organizations>."""
    orgs = []
    for orgs_elem in _find_descendants(root, 'organizations'):
        default = orgs_elem.get('default', '')
        for org in _find_children(orgs_elem, 'organization'):
            org_data = {
                'id': org.get('identifier', ''),
                'is_default': org.get('identifier', '') == default if default else True,
                'title': '',
                'items': [],
            }
            for child in org:
                tag = strip_ns(child.tag).lower()
                if tag == 'title':
                    org_data['title'] = (child.text or '').strip()
                elif tag == 'item':
                    org_data['items'].append(parse_item(child))
            orgs.append(org_data)
        break  # only the first <organizations> element is meaningful
    return orgs


def extract_resources(root):
    """Extract the resource inventory from <resources>."""
    resources = []
    for res_elem in _find_descendants(root, 'resources'):
        for res in _find_children(res_elem, 'resource'):
            resource = {
                'id': res.get('identifier', ''),
                'type': res.get('type', ''),
                'href': res.get('href', ''),
                'scorm_type': _local_attr(res, 'scormtype') or _local_attr(res, 'scormType') or '',
                'files': [],
                'dependencies': [],
            }
            for child in res:
                tag = strip_ns(child.tag).lower()
                if tag == 'file':
                    href = child.get('href', '')
                    if href:
                        resource['files'].append(href)
                elif tag == 'dependency':
                    ref = child.get('identifierref', '')
                    if ref:
                        resource['dependencies'].append(ref)
            resources.append(resource)
        break
    return resources


def flatten_items(items, depth=0):
    """Flatten the item tree into a list for counting/analysis."""
    flat = []
    for item in items:
        flat.append({'title': item.get('title', ''), 'depth': depth,
                     'id': item.get('id', ''),
                     'objectives': item.get('objectives', []),
                     'mastery_score': item.get('mastery_score', '')})
        flat.extend(flatten_items(item.get('children', []), depth + 1))
    return flat


def parse_manifest(manifest_path):
    """Parse an imsmanifest.xml. Returns dict with version, metadata,
    organizations and resources. Raises ET.ParseError on malformed XML."""
    manifest_path = Path(manifest_path)
    tree = ET.parse(str(manifest_path))
    root = tree.getroot()
    return {
        'scorm_version': detect_scorm_version(root),
        'metadata': extract_metadata(root, manifest_dir=manifest_path.parent),
        'organizations': extract_organizations(root),
        'resources': extract_resources(root),
    }
