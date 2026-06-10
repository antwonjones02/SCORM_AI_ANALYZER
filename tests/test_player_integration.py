"""Integration tests: actually play the fixture courses in headless Chromium.

Skipped automatically when no Chromium binary is resolvable."""

import pytest

from conftest import browser_available
from scormlib.package import safe_extract
from scormlib.player import play_package

pytestmark = pytest.mark.skipif(not browser_available(),
                                reason='no Chromium available for Playwright')


class TestClickThroughCourse:
    @pytest.fixture(scope='class')
    def play_result(self, basic_zip, tmp_path_factory):
        dest = tmp_path_factory.mktemp('play_basic')
        safe_extract(basic_zip, dest)
        shots = tmp_path_factory.mktemp('shots')
        return play_package(dest, 'index.html', screenshots_dir=shots,
                            max_slides=15, quiz_solver=None)

    def test_completes_naturally(self, play_result):
        assert play_result['error'] is None
        assert play_result['completed_naturally'] is True
        assert play_result['forced_completion'] is False

    def test_scorm_runtime_captured(self, play_result):
        rt = play_result['runtime']
        assert rt['completion_status'] in ('completed', 'passed')
        assert rt['lms_initialized'] is True
        assert rt['lms_finish_called'] is True
        assert rt['score_raw'] is not None
        assert rt['session_time'] == '0000:05:30'

    def test_quiz_interactions_recorded(self, play_result):
        rt = play_result['runtime']
        assert rt['interaction_count'] == 2
        first = rt['interactions']['0']
        assert first['type'] == 'choice'
        assert first['result'] in ('correct', 'wrong')

    def test_screenshots_and_slides(self, play_result):
        assert play_result['screenshots_captured'] >= 4
        types = [s['interaction_type'] for s in play_result['slides']]
        assert 'quiz' in types


class TestRiseStyleCourse:
    def test_scroll_completion(self, rise_zip, tmp_path):
        dest = tmp_path / 'rise'
        dest.mkdir()
        safe_extract(rise_zip, dest)
        result = play_package(dest, 'scormdriver/indexAPI.html',
                              screenshots_dir=tmp_path / 'shots', max_slides=15)
        assert result['error'] is None
        assert result['course_type'] == 'rise'
        assert result['runtime']['completion_status'] == 'completed'
        assert result['screenshots_captured'] >= 3


class TestScorm2004Course:
    def test_api_1484_11_captured(self, minimal_2004_zip, tmp_path):
        dest = tmp_path / 'm2004'
        dest.mkdir()
        safe_extract(minimal_2004_zip, dest)
        result = play_package(dest, 'index.html', max_slides=3)
        rt = result['runtime']
        assert rt['completion_status'] == 'completed'
        assert rt['lms_initialized'] is True
        assert rt['lms_finish_called'] is True
