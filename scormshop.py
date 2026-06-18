#!/usr/bin/env python3
"""SCORM Shop — the one-stop SCORM extractor.

Extracts, plays, scores and reports on SCORM 1.2 / SCORM 2004 / xAPI /
cmi5 / AICC packages. Works fully offline; add an API key for AI enrichment.

Usage:
    python3 scormshop.py analyze course.zip                 # extract + score
    python3 scormshop.py analyze course.zip --play          # + play in browser
    python3 scormshop.py analyze course.zip --play --llm    # + AI enrichment
    python3 scormshop.py analyze scorm_inbox/ --play        # whole folder
    python3 scormshop.py play course.zip                    # player only
    python3 scormshop.py report output/results/*.json       # rebuild HTML report

Outputs (default --out output/):
    output/<name>.json            full structured data (every data point)
    output/report.html            self-contained HTML report (screenshots embedded)
    output/screenshots/<name>/    slide screenshots from the player
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scormlib import analyze, save_result  # noqa: E402
from scormlib.report_html import write_html_report  # noqa: E402


def _collect_zips(target):
    target = Path(target)
    if target.is_dir():
        zips = sorted(target.glob('*.zip'))
        if not zips:
            sys.exit(f'❌ No .zip files found in {target}')
        return zips
    if not target.exists():
        sys.exit(f'❌ Not found: {target}')
    return [target]


def cmd_analyze(args):
    zips = _collect_zips(args.target)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for i, zip_path in enumerate(zips, 1):
        print(f'[{i}/{len(zips)}] {zip_path.name}')
        result = analyze(
            zip_path,
            use_llm=args.llm,
            use_video=args.video,
            use_player=args.play,
            llm_provider=args.llm_provider,
            whisper_model=args.whisper_model,
            screenshots_dir=(out_dir / 'screenshots' / zip_path.stem
                             if args.play else None),
            max_slides=args.max_slides,
            verbose=not args.quiet,
        )
        results.append(result)
        json_path = out_dir / f'{zip_path.stem}.json'
        save_result(result, json_path)
        score = result.get('stats', {}).get('ai_readiness_score')
        tier = result.get('stats', {}).get('ai_readiness_tier', '')
        err = f"  ⚠ {result['error']}" if result.get('error') else ''
        print(f'    → {json_path}  score: {score} ({tier}){err}')

    report_path = out_dir / 'report.html'
    write_html_report(results, report_path,
                      report_title=args.title,
                      embed_screenshots=not args.no_embed_screenshots)
    print(f'\n✅ HTML report: {report_path}')

    if args.pdf:
        try:
            from weasyprint import HTML
            pdf_path = report_path.with_suffix('.pdf')
            HTML(filename=str(report_path)).write_pdf(str(pdf_path))
            print(f'✅ PDF report:  {pdf_path}')
        except Exception as e:
            print(f'⚠️  PDF generation failed ({e}) — HTML report is still available')
    return results


def cmd_play(args):
    import shutil
    import tempfile

    from scormlib.package import (detect_package_type, find_entry_point,
                                  safe_extract)
    from scormlib.player import play_package

    zip_path = Path(args.target)
    out_dir = Path(args.out)
    extract_dir = tempfile.mkdtemp(prefix='scorm_play_')
    try:
        safe_extract(zip_path, extract_dir)
        entry = find_entry_point(extract_dir,
                                 package_type=detect_package_type(extract_dir))
        if not entry:
            sys.exit('❌ No HTML entry point found in package')
        print(f'🎮 Playing {zip_path.name} (entry: {entry})')
        result = play_package(
            extract_dir, entry,
            screenshots_dir=out_dir / 'screenshots' / zip_path.stem,
            max_slides=args.max_slides, verbose=not args.quiet)
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)

    out_path = out_dir / f'{zip_path.stem}_play.json'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))
    rt = result.get('runtime', {})
    print(f"  completion: {rt.get('completion_status')}  "
          f"score: {rt.get('score_raw')}  "
          f"interactions: {rt.get('interaction_count')}  "
          f"screenshots: {result.get('screenshots_captured')}")
    print(f'✅ Player report: {out_path}')


def cmd_report(args):
    results = []
    for pattern in args.inputs:
        for path in sorted(Path('.').glob(pattern)) or [Path(pattern)]:
            if not path.exists() or path.suffix != '.json':
                continue
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError as e:
                print(f'⚠️  Skipping {path}: {e}')
                continue
            if 'metadata' in data:
                results.append(data)
            elif 'courses' in data:
                print(f'⚠️  {path} is a legacy batch summary — '
                      'use generate_report.py for that format')
    if not results:
        sys.exit('❌ No analyzer JSON results found')
    out = Path(args.out)
    write_html_report(results, out, report_title=args.title)
    print(f'✅ HTML report: {out} ({len(results)} course(s))')


def main():
    parser = argparse.ArgumentParser(
        prog='scormshop',
        description='One-stop SCORM extractor: parse, play, score, report.')
    sub = parser.add_subparsers(dest='command', required=True)

    pa = sub.add_parser('analyze', help='Analyze package(s): extract, mine, score, report')
    pa.add_argument('target', help='SCORM .zip file or folder of zips')
    pa.add_argument('--play', action='store_true',
                    help='Play the course in a headless browser, capture runtime data')
    pa.add_argument('--llm', action='store_true', help='AI enrichment (needs API key)')
    pa.add_argument('--video', action='store_true',
                    help='Transcribe videos with Whisper and re-score')
    pa.add_argument('--llm-provider', default='claude', choices=['claude', 'deepseek'])
    pa.add_argument('--whisper-model', default='base',
                    choices=['tiny', 'base', 'small', 'medium', 'large'])
    pa.add_argument('--max-slides', type=int, default=40)
    pa.add_argument('--out', default='output', help='Output directory (default: output/)')
    pa.add_argument('--title', default='SCORM Extraction Report')
    pa.add_argument('--pdf', action='store_true', help='Also render the report as PDF')
    pa.add_argument('--no-embed-screenshots', action='store_true')
    pa.add_argument('--quiet', action='store_true')
    pa.set_defaults(func=cmd_analyze)

    pp = sub.add_parser('play', help='Player only: run the course, capture SCORM data')
    pp.add_argument('target', help='SCORM .zip file')
    pp.add_argument('--max-slides', type=int, default=40)
    pp.add_argument('--out', default='output')
    pp.add_argument('--quiet', action='store_true')
    pp.set_defaults(func=cmd_play)

    pr = sub.add_parser('report', help='Build HTML report from saved JSON results')
    pr.add_argument('inputs', nargs='+', help='Analyzer JSON files (globs ok)')
    pr.add_argument('--out', default='output/report.html')
    pr.add_argument('--title', default='SCORM Extraction Report')
    pr.set_defaults(func=cmd_report)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
