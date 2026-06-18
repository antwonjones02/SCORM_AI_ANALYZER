#!/usr/bin/env python3
"""
SCORM Analyzer
Parses SCORM 1.2 / SCORM 2004 / xAPI / cmi5 / AICC packages and extracts
structured metadata, mined content, and a deterministic AI-readiness score.

This is a thin CLI wrapper around the `scormlib` package — see scormlib/ for
the engine and scormshop.py for the full-featured CLI.

Usage:
    python3 scorm_analyzer.py <path_to_scorm.zip> [--llm] [--video] [--player]
                              [--llm-provider claude|deepseek] [--output out.json]
                              [--html-report report.html]

Options:
    --llm           Use AI to infer skills, generate summary (needs API key)
    --video         Extract + transcribe video (MP4) using Whisper, then re-score
    --player        Play the course in a headless browser, capture SCORM runtime
    --llm-provider  'claude' (default) or 'deepseek'
    --output        Save JSON to file (default: print to stdout)
    --html-report   Also write a self-contained HTML report
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scormlib import analyze  # noqa: E402


def analyze_scorm(zip_path, use_llm=False, use_video=False, use_player=False,
                  llm_provider='claude', whisper_model='base', **kwargs):
    """Backward-compatible entry point (used by batch_analyzer.py).

    `use_llm` may be True or an API key string (legacy behavior)."""
    api_key = use_llm if isinstance(use_llm, str) else None
    return analyze(
        zip_path,
        use_llm=bool(use_llm),
        use_video=use_video,
        use_player=use_player,
        llm_provider=llm_provider,
        whisper_model=whisper_model,
        api_key=api_key,
        **kwargs,
    )


def main():
    parser = argparse.ArgumentParser(
        description='SCORM Analyzer — extract structured data from SCORM packages')
    parser.add_argument('scorm_file', help='Path to SCORM .zip file')
    parser.add_argument('--llm', nargs='?', const=True,
                        help='Use AI to infer skills and generate summary '
                             '(optionally pass an API key)')
    parser.add_argument('--video', action='store_true',
                        help='Extract + transcribe video (MP4) with Whisper, then re-score')
    parser.add_argument('--player', action='store_true',
                        help='Play in headless Chromium, capture screenshots + SCORM runtime')
    parser.add_argument('--whisper-model', default='base',
                        choices=['tiny', 'base', 'small', 'medium', 'large'])
    parser.add_argument('--llm-provider', default='claude',
                        choices=['claude', 'deepseek'])
    parser.add_argument('--max-slides', type=int, default=40,
                        help='Max slides for the player pipeline (default: 40)')
    parser.add_argument('--output', help='Output JSON file path (default: stdout)')
    parser.add_argument('--html-report', help='Also write a self-contained HTML report')
    parser.add_argument('--verbose', action='store_true', help='Progress logging')
    args = parser.parse_args()

    result = analyze_scorm(
        args.scorm_file,
        use_llm=args.llm or args.video,  # --video implies --llm for baseline score
        use_video=args.video,
        use_player=args.player,
        llm_provider=args.llm_provider,
        whisper_model=args.whisper_model,
        max_slides=args.max_slides,
        verbose=args.verbose or bool(args.output),
    )

    output = json.dumps(result, indent=2, ensure_ascii=False)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(output)
        print(f'✅ Analysis saved to: {args.output}')
    else:
        print(output)

    if args.html_report:
        from scormlib.report_html import write_html_report
        write_html_report([result], args.html_report)
        print(f'✅ HTML report: {args.html_report}')


if __name__ == '__main__':
    main()
