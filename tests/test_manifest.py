import zipfile

import pytest

from scormlib.manifest import parse_duration_to_minutes, parse_manifest


@pytest.fixture(scope='module')
def parsed_basic(basic_zip, tmp_path_factory):
    dest = tmp_path_factory.mktemp('basic12')
    with zipfile.ZipFile(basic_zip) as zf:
        zf.extractall(dest)
    return parse_manifest(dest / 'imsmanifest.xml')


class TestDurationParsing:
    @pytest.mark.parametrize('raw,expected', [
        ('PT45M', 45.0),
        ('PT1H30M', 90.0),
        ('PT1H30M30S', 90.5),
        ('P1DT2H', 1560.0),
        ('0000:05:30', 5.5),
        ('01:30:00', 90.0),
        ('45', 45.0),
        ('45 min', 45.0),
        ('', None),
        ('garbage', None),
        ('PT', None),
    ])
    def test_cases(self, raw, expected):
        assert parse_duration_to_minutes(raw) == expected


class TestScorm12Manifest:
    def test_version(self, parsed_basic):
        assert parsed_basic['scorm_version'] == '1.2'

    def test_core_metadata(self, parsed_basic):
        meta = parsed_basic['metadata']
        assert meta['title'] == 'Workplace Fire Safety Essentials'
        assert 'fire' in meta['description'].lower()
        assert len(meta['keywords']) == 4
        assert meta['language'] == 'en'
        assert meta['duration_minutes'] == 45.0
        assert meta['author'] == 'Jane Trainer'
        assert 'Copyright' in meta['copyright']

    def test_organization_structure(self, parsed_basic):
        orgs = parsed_basic['organizations']
        assert len(orgs) == 1
        assert orgs[0]['title'] == 'Fire Safety Course'
        items = orgs[0]['items']
        assert [i['title'] for i in items] == \
            ['Introduction', 'Using Extinguishers', 'Final Quiz']

    def test_mastery_score(self, parsed_basic):
        quiz = parsed_basic['organizations'][0]['items'][2]
        assert quiz['mastery_score'] == '80'

    def test_resources(self, parsed_basic):
        resources = parsed_basic['resources']
        scos = [r for r in resources if r['scorm_type'] == 'sco']
        assert len(scos) == 1
        assert scos[0]['href'] == 'index.html'
        assert 'course.js' in scos[0]['files']
        assert scos[0]['dependencies'] == ['RES-ASSETS-SHARED']


class TestScorm2004Manifest:
    def test_version_and_sparse_metadata(self, minimal_2004_zip, tmp_path):
        with zipfile.ZipFile(minimal_2004_zip) as zf:
            zf.extractall(tmp_path)
        parsed = parse_manifest(tmp_path / 'imsmanifest.xml')
        assert parsed['scorm_version'].startswith('2004')
        meta = parsed['metadata']
        assert meta['title'] == 'Untitled Module 7'
        assert not meta['description']
        assert not meta['keywords']
        assert meta['duration_minutes'] is None
