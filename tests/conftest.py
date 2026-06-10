import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture(scope='session')
def fixtures_dir():
    return FIXTURES


@pytest.fixture(scope='session')
def basic_zip():
    path = FIXTURES / 'course_basic_12.zip'
    if not path.exists():
        pytest.skip('fixtures not built — run tests/fixtures/build_fixtures.py')
    return path


@pytest.fixture(scope='session')
def minimal_2004_zip():
    return FIXTURES / 'course_minimal_2004.zip'


@pytest.fixture(scope='session')
def rise_zip():
    return FIXTURES / 'course_rise_like.zip'


@pytest.fixture(scope='session')
def tincan_zip():
    return FIXTURES / 'course_tincan.zip'


def browser_available():
    """True if any Chromium binary is resolvable for the player tests."""
    try:
        from playwright.sync_api import sync_playwright
        from scormlib.player import resolve_browser
        with sync_playwright() as pw:
            browser = resolve_browser(pw)
            browser.close()
        return True
    except Exception:
        return False
