from scormlib import analyze
from scormlib.report_html import build_html_report, write_html_report


class TestHtmlReport:
    def test_single_course(self, basic_zip):
        result = analyze(basic_zip)
        html = build_html_report(result)
        assert 'Workplace Fire Safety Essentials' in html
        assert 'AI Ready' in html
        assert 'score breakdown' in html.lower()
        assert '<script' not in html.lower()  # self-contained, no JS needed

    def test_multi_course_with_errors(self, basic_zip, tmp_path):
        good = analyze(basic_zip)
        bad = {'source_file': 'broken.zip', 'error': 'Not a valid ZIP/SCORM file',
               'metadata': {}, 'stats': {}}
        out = write_html_report([good, bad], tmp_path / 'r.html')
        text = out.read_text()
        assert 'Not a valid ZIP' in text
        assert '2 package(s) analyzed' in text

    def test_escapes_html_in_metadata(self, tmp_path):
        evil = {'source_file': 'x.zip', 'package_type': 'scorm',
                'metadata': {'title': '<script>alert(1)</script>'},
                'stats': {'ai_readiness_score': 50,
                          'ai_readiness_tier': 'Partially Ready'},
                'organizations': [], 'resources': [], 'files': {}, 'content': {}}
        html = build_html_report(evil)
        assert '<script>alert(1)</script>' not in html
        assert '&lt;script&gt;' in html
