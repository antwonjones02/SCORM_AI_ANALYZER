"""scormlib — the engine behind the SCORM AI Analyzer.

Public API:
    analyze(zip_path, ...)         full pipeline (scormlib.analyzer)
    play_package(extract_dir, ...) headless course player (scormlib.player)
    build_html_report(results)     self-contained HTML report (scormlib.report_html)
"""

from .analyzer import analyze, save_result          # noqa: F401
from .scoring import readiness_score, score_to_tier  # noqa: F401

__version__ = '2.0.0'
