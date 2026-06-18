#!/usr/bin/env python3
"""
SCORM Player Agent
Renders a SCORM package in a real (headless) browser, navigates through it
like a learner, captures all LMS data, takes screenshots, and generates a
course report. Thin wrapper around scormlib.player — see scormshop.py for
the full-featured CLI.

Usage:
    python3 scorm_player.py <path_to_scorm.zip> [--output report.json]
                            [--screenshots DIR] [--max-slides N] [--no-vision]

Requirements:
    pip install playwright && playwright install chromium
    (optional) ANTHROPIC_API_KEY for vision analysis + AI quiz answers
"""

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scormlib import llm as llm_mod  # noqa: E402
from scormlib.package import (detect_package_type, find_entry_point,  # noqa: E402
                              safe_extract)
from scormlib.player import play_package  # noqa: E402


def play_scorm(zip_path, use_vision=True, max_slides=30, screenshots_dir=None):
    """Extract, serve, navigate, report. Returns a structured report dict."""
    report = {
        'source_file': str(zip_path),
        'slides': [],
        'scorm_data': {},
        'interactions': {},
        'completion_status': 'unknown',
        'score': None,
        'total_time_ms': 0,
        'course_summary': '',
        'skills_observed': [],
        'errors': [],
    }

    extract_dir = tempfile.mkdtemp(prefix='scorm_play_')
    try:
        print('📦 Extracting SCORM package...')
        safe_extract(zip_path, extract_dir)
        entry = find_entry_point(extract_dir,
                                 package_type=detect_package_type(extract_dir))
        if not entry:
            report['errors'].append('No HTML entry point found')
            return report
        print(f'🎯 Entry point: {entry}')

        print('🚀 Launching browser...')
        result = play_package(extract_dir, entry, screenshots_dir=screenshots_dir,
                              max_slides=max_slides, verbose=True)
        if result.get('error'):
            report['errors'].append(result['error'])

        rt = result.get('runtime', {})
        report['slides'] = result.get('slides', [])
        report['scorm_data'] = rt.get('cmi_data', {})
        report['interactions'] = rt.get('interactions', {})
        report['completion_status'] = rt.get('completion_status', 'unknown')
        report['score'] = rt.get('score_raw')
        report['total_time_ms'] = rt.get('elapsed_ms', 0)
        report['course_type'] = result.get('course_type')
        report['completed_naturally'] = result.get('completed_naturally')

        if use_vision and llm_mod.llm_available('claude') and result.get('slides'):
            print('🧠 Vision-analyzing captured slides...')
            analysis = llm_mod.vision_analyze_slides(
                result['slides'], Path(zip_path).stem)
            report['ai_analysis'] = analysis
            report['course_summary'] = analysis.get('ai_summary', '')
            report['skills_observed'] = analysis.get('inferred_skills', [])
    except Exception as e:
        report['errors'].append(f'Fatal error: {e}')
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)

    return report


def main():
    parser = argparse.ArgumentParser(
        description='SCORM Player Agent — navigate and report on SCORM courses')
    parser.add_argument('scorm_file', help='Path to SCORM .zip file')
    parser.add_argument('--output', help='Output JSON file path (default: stdout)')
    parser.add_argument('--screenshots', help='Directory to save slide screenshots')
    parser.add_argument('--no-vision', action='store_true',
                        help='Skip vision analysis (deterministic navigation only)')
    parser.add_argument('--max-slides', type=int, default=30,
                        help='Max slides to navigate (default: 30)')
    args = parser.parse_args()

    use_vision = not args.no_vision
    if use_vision and not llm_mod.llm_available('claude'):
        print('⚠️  No ANTHROPIC_API_KEY set. Running without vision analysis.')
        use_vision = False

    report = play_scorm(args.scorm_file, use_vision=use_vision,
                        max_slides=args.max_slides,
                        screenshots_dir=args.screenshots)

    output = json.dumps(report, indent=2)
    if args.output:
        Path(args.output).write_text(output)
        print(f'\n✅ Report saved to: {args.output}')
    else:
        print(output)


if __name__ == '__main__':
    main()
