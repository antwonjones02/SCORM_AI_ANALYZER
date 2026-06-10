from scormlib import analyze
from scormlib.content import (extract_content, parse_caption_file,
                              parse_js_strings, parse_storyline_js)
from scormlib.package import safe_extract
from scormlib.scoring import readiness_score, score_to_tier


class TestContentMining:
    def test_js_string_harvest_finds_quiz(self, basic_zip, tmp_path):
        safe_extract(basic_zip, tmp_path)
        content = extract_content(tmp_path)
        assert content['word_count'] > 200
        assert any('PASS' in q for q in content['quiz_questions'])
        assert 'js_strings' in content['sources']

    def test_html_text_extraction(self, rise_zip, tmp_path):
        safe_extract(rise_zip, tmp_path)
        content = extract_content(tmp_path)
        assert content['word_count'] > 300
        assert 'html' in content['sources']
        assert content['slide_titles']  # section headings

    def test_vtt_parsing(self, tmp_path):
        vtt = tmp_path / 'cap.vtt'
        vtt.write_text(
            'WEBVTT\n\n00:00.000 --> 00:03.000\nWelcome to the course.\n\n'
            '00:03.000 --> 00:06.000\n<v Speaker>Safety first.</v>\n')
        assert parse_caption_file(vtt) == 'Welcome to the course. Safety first.'

    def test_srt_parsing(self, tmp_path):
        srt = tmp_path / 'cap.srt'
        srt.write_text('1\n00:00:00,000 --> 00:00:03,000\nHello there.\n\n'
                       '2\n00:00:03,000 --> 00:00:06,000\nHello there.\n')
        # consecutive duplicates collapse
        assert parse_caption_file(srt) == 'Hello there.'

    def test_storyline_globalprovidedata(self, tmp_path):
        js = tmp_path / 'slide1.js'
        payload = ('{"title":"Slide One","objects":[{"text":'
                   '"Always wear protective equipment in the lab."}]}')
        escaped = payload.replace('"', '\\"') if False else payload
        js.write_text(f"window.globalProvideData('slide', '{escaped}');")
        texts, _questions, titles, _quiz_items, _dur = parse_storyline_js(js)
        assert 'Slide One' in titles
        assert any('protective equipment' in t for t in texts)

    def test_js_strings_skips_minified(self, tmp_path):
        js = tmp_path / 'lib.min.js'
        js.write_text('var a=' + '"This is a long sentence that looks like prose '
                      'but lives in a minified bundle.";' * 50)
        assert parse_js_strings(js) == []


class TestScoring:
    def test_rich_package_beats_sparse(self, basic_zip, minimal_2004_zip):
        rich = analyze(basic_zip)
        sparse = analyze(minimal_2004_zip)
        assert rich['stats']['ai_readiness_score'] >= 70
        assert sparse['stats']['ai_readiness_score'] <= 20
        assert rich['stats']['ai_readiness_tier'] == 'AI Ready'
        assert sparse['stats']['ai_readiness_tier'] == 'Not Ready'

    def test_breakdown_is_complete(self, basic_zip):
        result = analyze(basic_zip)
        breakdown = result['stats']['score_breakdown']
        assert sum(b['max'] for b in breakdown.values()) == 100
        for b in breakdown.values():
            assert 0 <= b['points'] <= b['max']
            assert b['note']

    def test_empty_inputs(self):
        score, breakdown = readiness_score({}, [], {})
        assert score == 0
        assert breakdown

    def test_tiers(self):
        assert score_to_tier(85) == 'AI Ready'
        assert score_to_tier(50) == 'Partially Ready'
        assert score_to_tier(10) == 'Not Ready'
        assert score_to_tier(None) == 'Unknown'


class TestAnalyzeOrchestrator:
    def test_full_result_schema(self, basic_zip):
        result = analyze(basic_zip)
        assert result.get('error') is None
        # legacy keys preserved for old report generators
        for key in ('source_file', 'scorm_version', 'metadata', 'organizations',
                    'resources', 'stats', 'llm_analysis', 'player_pipeline'):
            assert key in result
        # new v2 keys
        for key in ('package_type', 'authoring_tool', 'files', 'content',
                    'sha256', 'analyzed_at'):
            assert key in result
        assert result['files']['entry_point'] == 'index.html'

    def test_xapi_package(self, tincan_zip):
        result = analyze(tincan_zip)
        assert result['package_type'] == 'xapi'
        assert result['metadata']['title'] == 'Cyber Hygiene Fundamentals'

    def test_invalid_zip(self, tmp_path):
        bad = tmp_path / 'bad.zip'
        bad.write_text('this is not a zip')
        result = analyze(bad)
        assert 'error' in result and 'ZIP' in result['error']

    def test_missing_file(self):
        result = analyze('/nonexistent/nope.zip')
        assert 'error' in result

    def test_legacy_wrapper_signature(self, basic_zip):
        from scorm_analyzer import analyze_scorm
        result = analyze_scorm(str(basic_zip))
        assert result['metadata']['title'] == 'Workplace Fire Safety Essentials'
