#!/usr/bin/env python3
"""
SCORM Batch Analyzer
Processes a folder of SCORM ZIPs and outputs per-course JSON + a master PDF report.

Usage:
    python3 batch_analyzer.py <folder_of_zips> [--llm] [--video] [--player]

Options:
    --llm       Use Claude to enrich each course (requires ANTHROPIC_API_KEY)
    --video     Transcribe MP4s with Whisper and re-score
    --player    Run course in headless browser, capture screenshots, vision-analyze
"""

import os
import sys
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

# Import the single-file analyzer
sys.path.insert(0, str(Path(__file__).parent))
from scorm_analyzer import analyze_scorm

WORKSPACE = Path('/home/ubuntu/.openclaw/workspace')
OUTPUT_BASE = WORKSPACE / 'output'


def score_to_tier(score):
    if score is None:
        return 'Unknown'
    if score >= 70:
        return 'Good'
    elif score >= 40:
        return 'Needs Work'
    else:
        return 'Not Ready'


def get_score(data):
    """Extract primary AI readiness score from analysis result."""
    # Player pipeline score takes priority if available
    player = data.get('player_pipeline') or {}
    if player.get('score_after') is not None:
        return player['score_after']

    # Video pipeline
    video = data.get('video_pipeline') or {}
    if video.get('score_after') is not None:
        return video['score_after']

    # LLM analysis
    llm = data.get('llm_analysis') or {}
    if isinstance(llm.get('ai_readiness_score'), (int, float)):
        return llm['ai_readiness_score']

    return None


def get_score_label(data, use_video, use_player):
    """Build score display string showing before/after if pipelines ran."""
    llm = data.get('llm_analysis') or {}
    base = llm.get('ai_readiness_score')

    player = data.get('player_pipeline') or {}
    video = data.get('video_pipeline') or {}

    if use_player and player.get('score_after') is not None:
        pb = player.get('score_before', base)
        pa = player['score_after']
        return f"{pb} → {pa} (player)"
    elif use_video and video.get('score_after') is not None:
        vb = video.get('score_before', base)
        va = video['score_after']
        return f"{vb} → {va} (video)"
    elif base is not None:
        return str(base)
    else:
        return 'N/A'


def normalize_for_report(data, zip_name):
    """Convert full analyzer output to course summary dict for report."""
    llm = data.get('llm_analysis') or {}
    player = data.get('player_pipeline') or {}
    video = data.get('video_pipeline') or {}
    score = get_score(data)

    return {
        'file': zip_name,
        'title': data['metadata'].get('title') or Path(data.get('source_file', '')).stem,
        'scorm_version': data.get('scorm_version', '—'),
        'has_description': data.get('stats', {}).get('has_description', False),
        'has_keywords': data.get('stats', {}).get('has_keywords', False),
        'keyword_count': data.get('stats', {}).get('keyword_count', 0),
        'total_modules': data.get('stats', {}).get('total_modules', 0),
        'sco_count': data.get('stats', {}).get('sco_count', 0),
        'ai_readiness_score': score,
        'ai_readiness_tier': score_to_tier(score),
        'inferred_skills': llm.get('inferred_skills', []),
        'quality_flags': llm.get('content_quality_flags', []),
        'ai_readiness_notes': llm.get('ai_readiness_notes', ''),
        'difficulty_level': llm.get('difficulty_level', ''),
        'target_audience': llm.get('target_audience', ''),
        'ai_summary': llm.get('ai_summary', ''),
        'learning_objectives': llm.get('learning_objectives', []),
        'estimated_duration_minutes': llm.get('estimated_duration_minutes'),
        'has_player': bool(player.get('screenshots')),
        'player_score_before': player.get('score_before'),
        'player_score_after': player.get('score_after'),
        'has_video': bool(video.get('transcripts')),
        'video_score_before': video.get('score_before'),
        'video_score_after': video.get('score_after'),
    }


def run_batch(folder, use_llm=False, use_video=False, use_player=False, llm_provider='claude'):
    folder = Path(folder)
    zip_files = sorted(folder.glob('*.zip'))

    if not zip_files:
        print(f"❌ No ZIP files found in: {folder}")
        sys.exit(1)

    date_str = datetime.utcnow().strftime('%Y-%m-%d')
    batch_dir = OUTPUT_BASE / f'batch_{date_str}'
    batch_dir.mkdir(parents=True, exist_ok=True)

    n = len(zip_files)
    print(f"📦 Found {n} SCORM package(s) in {folder}")
    print(f"🔍 LLM: {'ON (' + llm_provider + ')' if use_llm else 'OFF'}  |  Video: {'ON' if use_video else 'OFF'}  |  Player: {'ON' if use_player else 'OFF'}")
    print(f"📁 Output: {batch_dir}")
    print()

    results = []
    errors = []
    course_summaries = []

    for i, zip_path in enumerate(zip_files, 1):
        course_name = zip_path.stem
        print(f"[{i}/{n}] {course_name} ...", end=' ', flush=True)
        try:
            data = analyze_scorm(
                str(zip_path),
                use_llm=use_llm or use_video or use_player,
                use_video=use_video,
                use_player=use_player,
                llm_provider=llm_provider
            )

            if 'error' in data:
                print(f"⚠️  {data['error']}")
                errors.append({'file': zip_path.name, 'error': data['error']})
                continue

            # Save individual JSON
            safe_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in course_name)
            out_file = batch_dir / f'{safe_name}.json'
            out_file.write_text(json.dumps(data, indent=2))

            # Score display
            score_label = get_score_label(data, use_video, use_player)
            print(f"✅  score: {score_label}")

            results.append(data)
            course_summaries.append(normalize_for_report(data, zip_path.name))

        except Exception as e:
            print(f"💥  Exception: {str(e)}")
            errors.append({'file': zip_path.name, 'error': str(e)})

    # Build batch summary
    scores = [c['ai_readiness_score'] for c in course_summaries if isinstance(c.get('ai_readiness_score'), (int, float))]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    tier_counts = {'Good': 0, 'Needs Work': 0, 'Not Ready': 0, 'Unknown': 0}
    for c in course_summaries:
        t = c.get('ai_readiness_tier', 'Unknown')
        tier_counts[t] = tier_counts.get(t, 0) + 1

    # Save master batch JSON (with courses key for generate_report.py)
    master = {
        'generated_at': datetime.utcnow().isoformat() + 'Z',
        'folder': str(folder),
        'total_packages': n,
        'processed': len(results),
        'errors': len(errors),
        'llm_enriched': use_llm or use_video or use_player,
        'average_ai_readiness_score': avg_score,
        'score_distribution': tier_counts,
        'courses': course_summaries,
        'error_log': errors,
    }
    master_json = batch_dir / 'batch_summary.json'
    master_json.write_text(json.dumps(master, indent=2))

    # Print summary table
    print()
    print('=' * 62)
    print(f"  BATCH COMPLETE")
    print('=' * 62)
    print(f"  Processed:   {len(results)}/{n}")
    print(f"  Errors:      {len(errors)}")
    if avg_score:
        print(f"  Avg Score:   {avg_score}/100")
    print()
    print(f"  {'COURSE':<35} {'SCORE':>8}  {'TIER':<12}")
    print(f"  {'-'*35} {'-'*8}  {'-'*12}")
    for c in course_summaries:
        title = (c.get('title') or c.get('file', ''))[:34]
        score_str = str(c.get('ai_readiness_score', '—'))
        tier = c.get('ai_readiness_tier', '—')
        print(f"  {title:<35} {score_str:>8}  {tier:<12}")
    if errors:
        print()
        print(f"  ERRORS:")
        for e in errors:
            print(f"  ⚠  {e['file']}: {e['error'][:60]}")
    print('=' * 62)
    print(f"  Batch JSON:  {master_json}")

    # Generate PDF report
    print()
    print("📑 Generating PDF report...")
    try:
        report_script = Path(__file__).parent / 'generate_report.py'
        pdf_path = OUTPUT_BASE / f'scorm_report_{date_str}.pdf'
        result = subprocess.run(
            [sys.executable, str(report_script), str(master_json),
             '--output', str(pdf_path), '--client', 'Organization'],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            print(f"✅  PDF Report: {pdf_path}")
        else:
            print(f"⚠️  PDF generation issue: {result.stderr[:200]}")
            # Try direct import as fallback
            sys.path.insert(0, str(Path(__file__).parent))
            from generate_report import load_course_data, build_html
            from weasyprint import HTML
            courses, _ = load_course_data(str(master_json))
            html = build_html(courses, 'Organization', date_str)
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            HTML(string=html).write_pdf(str(pdf_path))
            print(f"✅  PDF Report: {pdf_path}")
    except Exception as e:
        print(f"⚠️  PDF generation failed: {e}")

    return master


def main():
    parser = argparse.ArgumentParser(description='SCORM Batch Analyzer — audit an entire course catalog')
    parser.add_argument('folder', help='Folder containing SCORM .zip files')
    parser.add_argument('--llm', action='store_true', help='Use AI enrichment for each course')
    parser.add_argument('--video', action='store_true', help='Transcribe MP4s with Whisper and re-score')
    parser.add_argument('--player', action='store_true', help='Run in headless browser and vision-analyze')
    parser.add_argument('--llm-provider', default='claude', choices=['claude', 'deepseek'],
                        help='AI provider: claude or deepseek')
    args = parser.parse_args()

    run_batch(
        args.folder,
        use_llm=args.llm,
        use_video=args.video,
        use_player=args.player,
        llm_provider=args.llm_provider
    )


if __name__ == '__main__':
    main()
