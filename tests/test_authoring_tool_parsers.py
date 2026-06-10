"""Tests for the authoring-tool-specific deterministic parsers, built from
the verified format reference (Storyline, Rise, Captivate, iSpring, Camtasia)."""

import base64
import json
import zlib

from scormlib.content import (extract_content, parse_camtasia_config,
                              parse_captivate, parse_ispring,
                              parse_rise_index, parse_storyline_js,
                              parse_storyline_meta)


class TestStoryline:
    def test_slide_with_quiz_interaction(self, tmp_path):
        slide = {
            'title': 'Check Your Knowledge',
            'slideLayers': [{'isBaseLayer': True,
                             'timeline': {'duration': 7477}}],
            'interactions': [{
                'id': 'i1', 'lmsId': 'MultiChoice', 'type': 'multiplechoice',
                'lmstext': 'Which agent puts out electrical fires safely?',
                'choices': [
                    {'id': 'choice_1', 'lmstext': 'Water'},
                    {'id': 'choice_2', 'lmstext': 'CO2 extinguisher'},
                ],
                'answers': [{'id': 'a1', 'status': 'correct',
                             'evaluate': {'statements': [
                                 {'choiceid': 'choices.choice_2'}]}}],
            }],
        }
        payload = json.dumps(slide).replace('\\', '\\\\').replace("'", "\\'")
        js = tmp_path / 'slide1.js'
        js.write_text(f"window.globalProvideData('slide', '{payload}');")

        texts, _q, titles, quiz_items, dur_ms = parse_storyline_js(js)
        assert 'Check Your Knowledge' in titles
        assert dur_ms == 7477
        assert quiz_items[0]['question'] == \
            'Which agent puts out electrical fires safely?'
        assert 'CO2 extinguisher' in quiz_items[0]['options']

    def test_untitled_slide_filtered(self, tmp_path):
        js = tmp_path / 's.js'
        js.write_text("window.globalProvideData('slide', "
                      "'{\"title\":\"Untitled Slide\"}');")
        _t, _q, titles, _qi, _d = parse_storyline_js(js)
        assert titles == []

    def test_caption_wrapper_percent_encoded_vtt(self, tmp_path):
        vtt = 'WEBVTT%0D%0A%0D%0A00%3A00.000%20--%3E%2000%3A03.000%0D%0AStay%20calm%20and%20evacuate.'
        js = tmp_path / 'abc_captions.js'
        js.write_text("window.globalProvideData('caption', "
                      f"'{{\"data\":\"{vtt}\"}}');")
        texts, _q, _t, _qi, _d = parse_storyline_js(js)
        assert any('Stay calm and evacuate.' in t for t in texts)

    def test_unicode_not_mangled(self, tmp_path):
        js = tmp_path / 's.js'
        js.write_text("window.globalProvideData('slide', "
                      "'{\"title\":\"Sécurité incendie — étape 1\"}');")
        _t, _q, titles, _qi, _d = parse_storyline_js(js)
        assert titles == ['Sécurité incendie — étape 1']

    def test_meta_xml(self, tmp_path):
        (tmp_path / 'meta.xml').write_text(
            '<?xml version="1.0"?><meta><project title="Forklift Basics" '
            'duration="About 12 minutes"><slidemeta viewslides="23" '
            'slidecountdescription="23 slides"/></project>'
            '<application name="Articulate Storyline" version="3.94"/></meta>')
        meta = parse_storyline_meta(tmp_path)
        assert meta['title'] == 'Forklift Basics'
        assert meta['duration_text'] == 'About 12 minutes'
        assert meta['slide_count'] == '23'


class TestRise:
    def _make_course(self):
        return {
            'course': {
                'title': 'Data Handling 101',
                'description': '<p>Learn safe data handling.</p>',
                'lessons': [
                    {'id': '1', 'title': 'Part One', 'type': 'section'},
                    {'id': '2', 'title': 'Why Privacy Matters', 'type': 'lesson',
                     'items': [{'family': 'knowledgeCheck',
                                'title': 'Which of these is PII?',
                                'answers': [
                                    {'title': 'A favorite color', 'correct': False},
                                    {'title': 'A passport number', 'correct': True},
                                ]}]},
                ],
            }
        }

    def test_window_coursedata_encoding(self, tmp_path):
        b64 = base64.b64encode(
            json.dumps(self._make_course()).encode()).decode()
        index = tmp_path / 'index.html'
        index.write_text(f'<script>window.courseData = "{b64}"</script>')
        texts, _q, lessons, quiz_items, title, desc = parse_rise_index(index)
        assert title == 'Data Handling 101'
        assert desc == 'Learn safe data handling.'
        assert lessons == ['Why Privacy Matters']  # section headers excluded
        assert quiz_items[0]['correct'] == ['A passport number']

    def test_resolvejsonp_encoding(self, tmp_path):
        b64 = base64.b64encode(
            json.dumps(self._make_course()).encode()).decode()
        (tmp_path / 'scormcontent' / 'locales').mkdir(parents=True)
        (tmp_path / 'scormcontent' / 'index.html').write_text('<html></html>')
        (tmp_path / 'scormcontent' / 'locales' / 'und.js').write_text(
            f'__resolveJsonp("course:und", "{b64}")')
        _t, _q, _l, _qi, title, _d = parse_rise_index(
            tmp_path / 'scormcontent' / 'index.html', tmp_path)
        assert title == 'Data Handling 101'


class TestCaptivate:
    def test_project_txt_and_cpm(self, tmp_path):
        project = {
            'metadata': {'generator': 'Captivate', 'generatorVersion': '12.4.0',
                         'title': 'Compliance Module', 'durationInFrames': 3225,
                         'frameRate': 30, 'totalSlides': 12},
            'toc': [{'id': 's1', 'title': 'Welcome'},
                    {'id': 's2', 'title': 'Policies'}],
            'contentStructure': [
                {'id': 'Slide1911', 'class': 'Question Slide',
                 'roles': {'question': {
                     'text': 'What is Microsoft Copilot used for?',
                     'interactionType': 'choice',
                     'correctAnswers': ['si2095']}}},
            ],
        }
        (tmp_path / 'project.txt').write_text(json.dumps(project))
        (tmp_path / 'assets' / 'js').mkdir(parents=True)
        (tmp_path / 'assets' / 'js' / 'CPM.js').write_text(
            "cp.D={si2095:{accstr:'To draft documents using AI assistance',"
            "b:[0,0,10,10]},x2:{accstr:'Rectangle 12',b:[1,1,2,2]}}")

        texts, titles, quiz_items, meta = parse_captivate(tmp_path)
        assert meta['title'] == 'Compliance Module'
        assert meta['duration_minutes'] == round(3225 / 30 / 60, 2)
        assert titles == ['Welcome', 'Policies']
        assert quiz_items[0]['question'] == 'What is Microsoft Copilot used for?'
        assert any('draft documents' in t for t in texts)
        assert not any('Rectangle' in t for t in texts)  # shape names filtered


class TestISpring:
    def test_presinfo_zlib_and_quizinfo_plain(self, tmp_path):
        pres = {'t': 'Sales Onboarding', 'ui': 'issuite_11.15.7',
                's': [{'t': 'Welcome', 'x': 'Welcome to sales onboarding.\r\nLet us begin.'},
                      {'t': 'Goals', 'x': 'Understand the quarterly goals.'}]}
        b64 = base64.b64encode(zlib.compress(json.dumps(pres).encode())).decode()
        (tmp_path / 'index.html').write_text(
            f'<script>var presInfo = "{b64}";</script>')

        quiz = {'d': {'T': 'Final Check', 'sl': {'g': [{'S': [
            {'i': 'q1', 'tp': 'MultipleChoice',
             'D': {'a': 'What is the discount approval limit?',
                   'h': '<p>What is the discount approval limit?</p>'}}]}]}}}
        qb64 = base64.b64encode(json.dumps(quiz).encode()).decode()
        (tmp_path / 'data').mkdir()
        (tmp_path / 'data' / 'quiz1.js').write_text(f'var quizInfo = "{qb64}";')

        texts, titles, quiz_items, meta = parse_ispring(tmp_path)
        assert meta['title'] == 'Sales Onboarding'
        assert 'Welcome' in titles and 'Goals' in titles
        assert any('quarterly goals' in t for t in texts)
        assert quiz_items[0]['question'] == 'What is the discount approval limit?'


class TestCamtasia:
    def test_config_duration_and_title(self, tmp_path):
        (tmp_path / 'video_config.xml').write_text(
            '<rdf xmlns:tsc="http://www.techsmith.com/xmp/tsc/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/">'
            '<dc:title>Quarterly Update</dc:title>'
            '<xmpDM:duration xmpDM:scale="1/1000" xmpDM:value="54200"/></rdf>')
        meta = parse_camtasia_config(tmp_path)
        assert meta['title'] == 'Quarterly Update'
        assert meta['duration_minutes'] == round(54200 / 1000 / 60, 2)


class TestIntegrationIntoExtractContent:
    def test_storyline_package_shape(self, tmp_path):
        (tmp_path / 'html5' / 'data' / 'js').mkdir(parents=True)
        slide = {'title': 'Lifting Safely',
                 'slideLayers': [{'timeline': {'duration': 120000}}],
                 'objects': [{'text': 'Bend your knees, not your back, '
                                      'when lifting heavy loads.'}]}
        payload = json.dumps(slide).replace("'", "\\'")
        (tmp_path / 'html5' / 'data' / 'js' / 'aaa.js').write_text(
            f"window.globalProvideData('slide', '{payload}');")
        (tmp_path / 'meta.xml').write_text(
            '<meta><project title="Manual Handling" duration="About 2 minutes"/>'
            '</meta>')

        content = extract_content(tmp_path)
        assert 'Lifting Safely' in content['slide_titles']
        assert content['embedded_metadata']['title'] == 'Manual Handling'
        assert content['estimated_duration_minutes'] == 2.0
        assert 'storyline_data' in content['sources']
        assert 'storyline_meta' in content['sources']
