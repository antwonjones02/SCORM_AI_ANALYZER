import io
import zipfile

from scormlib.package import (build_inventory, detect_authoring_tool,
                              detect_package_type, find_entry_point,
                              parse_tincan, safe_extract)


class TestPackageTypeDetection:
    def test_scorm(self, basic_zip, tmp_path):
        safe_extract(basic_zip, tmp_path)
        assert detect_package_type(tmp_path)['type'] == 'scorm'

    def test_xapi(self, tincan_zip, tmp_path):
        safe_extract(tincan_zip, tmp_path)
        info = detect_package_type(tmp_path)
        assert info['type'] == 'xapi'
        parsed = parse_tincan(info['descriptor'])
        assert parsed['title'] == 'Cyber Hygiene Fundamentals'
        assert parsed['launch'] == 'index.html'
        assert parsed['activity_id'].startswith('http://example.com/xapi')

    def test_unknown(self, tmp_path):
        (tmp_path / 'random.txt').write_text('nothing here')
        assert detect_package_type(tmp_path)['type'] == 'unknown'


class TestZipSlipProtection:
    def test_traversal_entries_are_skipped(self, tmp_path):
        evil = io.BytesIO()
        with zipfile.ZipFile(evil, 'w') as zf:
            zf.writestr('good.txt', 'ok')
            zf.writestr('../../evil.txt', 'pwned')
        zip_path = tmp_path / 'evil.zip'
        zip_path.write_bytes(evil.getvalue())

        dest = tmp_path / 'dest'
        dest.mkdir()
        extracted = safe_extract(zip_path, dest)

        assert 'good.txt' in extracted
        assert (dest / 'good.txt').exists()
        assert not (tmp_path / 'evil.txt').exists()
        assert not (tmp_path.parent / 'evil.txt').exists()


class TestAuthoringToolDetection:
    def test_rise_structure(self, rise_zip, tmp_path):
        safe_extract(rise_zip, tmp_path)
        tool = detect_authoring_tool(tmp_path)
        assert tool['name'] == 'Articulate Rise'
        assert tool['confidence'] == 'high'

    def test_no_false_positive_on_queryselectorall(self, tmp_path):
        # "querySelectorAll" contains the substring "lectorA"
        (tmp_path / 'app.js').write_text(
            "document.querySelectorAll('input').forEach(x => x);")
        (tmp_path / 'index.html').write_text('<html><body>hi</body></html>')
        assert detect_authoring_tool(tmp_path)['name'] == 'unknown'

    def test_storyline_fingerprint(self, tmp_path):
        (tmp_path / 'html5' / 'data' / 'js').mkdir(parents=True)
        (tmp_path / 'html5' / 'data' / 'js' / 'data.js').write_text('x')
        assert detect_authoring_tool(tmp_path)['name'] == 'Articulate Storyline'


class TestEntryPointAndInventory:
    def test_entry_from_manifest_sco(self, basic_zip, tmp_path):
        safe_extract(basic_zip, tmp_path)
        from scormlib.manifest import parse_manifest
        parsed = parse_manifest(tmp_path / 'imsmanifest.xml')
        assert find_entry_point(tmp_path, parsed['resources']) == 'index.html'

    def test_inventory_counts(self, basic_zip, tmp_path):
        safe_extract(basic_zip, tmp_path)
        inv = build_inventory(tmp_path)
        assert inv['total_files'] == 4
        assert inv['by_extension']['.js']['count'] == 1
        assert not inv['has_video']
