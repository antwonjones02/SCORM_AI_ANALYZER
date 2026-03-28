#!/usr/bin/env python3
"""
SCORM AI Readiness Report Generator
Generates a consulting-grade PDF from analyzed SCORM course JSON files.

Usage:
    python3 generate_report.py [json_file_or_folder] [--output report.pdf] [--client "Org Name"]

Input:
    - Single JSON file (output from scorm_analyzer.py --output)
    - Folder containing multiple JSON files
    - Reads from scorm_inbox/*.json by default if no arg given

Output:
    - PDF at /home/ubuntu/.openclaw/workspace/output/scorm_report_YYYY-MM-DD.pdf
"""

import json
import argparse
import sys
import os
from pathlib import Path
from datetime import datetime

try:
    from weasyprint import HTML, CSS
except ImportError:
    print("❌ WeasyPrint not installed. Run: pip install weasyprint")
    sys.exit(1)

# Colors
R = "#C8102E"
B = "#003087"
D = "#2D2D2D"
M = "#666666"
L = "#F5F5F5"
G = "#28a745"
Y = "#FF9800"

WORKSPACE = Path('/home/ubuntu/.openclaw/workspace')
OUTPUT_DIR = WORKSPACE / 'output'


def score_to_tier(score):
    if score is None:
        return 'Unknown'
    if score >= 70:
        return 'Good'
    elif score >= 40:
        return 'Needs Work'
    else:
        return 'Not Ready'


def score_color(score):
    if score is None:
        return M
    if score >= 70:
        return G
    elif score >= 40:
        return Y
    else:
        return R


def tier_color(tier):
    return {
        'AI Ready': G, 'Good': G,
        'Needs Work': Y,
        'Not Ready': R,
        'Unknown': M,
    }.get(tier, M)


def score_badge(score):
    if score is None:
        return f'<span style="font-size:32pt;font-weight:700;color:{M};">N/A</span>'
    color = score_color(score)
    return f'<span style="font-size:40pt;font-weight:700;color:{color};">{score}</span><span style="font-size:12pt;color:{M};">/100</span>'


def load_course_data(path):
    """
    Load course data from:
    - A single scorm_analyzer JSON output file
    - A batch_report JSON (has 'courses' key)
    - A folder of JSON files
    Returns list of normalized course dicts.
    """
    p = Path(path)
    json_files = []

    if p.is_dir():
        json_files = sorted(p.glob('*.json'))
    elif p.is_file():
        json_files = [p]
    else:
        print(f"❌ Path not found: {path}")
        sys.exit(1)

    courses = []
    raw_results = []  # full analyzer output dicts

    for jf in json_files:
        try:
            data = json.loads(jf.read_text())
        except Exception as e:
            print(f"⚠️  Skipping {jf.name}: {e}")
            continue

        # Batch report format (has 'courses' list)
        if 'courses' in data and isinstance(data.get('courses'), list):
            for c in data['courses']:
                courses.append(c)
            # Also keep raw results if present
            if 'raw_results' in data:
                raw_results.extend(data['raw_results'])
        # Single analyzer output format (has 'metadata' key)
        elif 'metadata' in data:
            raw_results.append(data)
            # Normalize to course summary dict
            llm = data.get('llm_analysis') or {}
            player = data.get('player_pipeline') or {}
            video = data.get('video_pipeline') or {}
            score = llm.get('ai_readiness_score')
            courses.append({
                'file': Path(data.get('source_file', jf.name)).name,
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
                # Pipeline info
                'has_player': bool(player.get('screenshots')),
                'player_score_before': player.get('score_before'),
                'player_score_after': player.get('score_after'),
                'has_video': bool(video.get('transcripts')),
                'video_score_before': video.get('score_before'),
                'video_score_after': video.get('score_after'),
                '_raw': data,
            })

    return courses, raw_results


def build_course_section(c, idx):
    """Build per-course HTML section."""
    score = c.get('ai_readiness_score')
    tier = c.get('ai_readiness_tier', 'Unknown')
    t_color = tier_color(tier)
    title = c.get('title') or c.get('file', 'Untitled')
    skills = c.get('inferred_skills', [])
    flags = c.get('quality_flags', [])
    objectives = c.get('learning_objectives', [])
    summary = c.get('ai_summary', '') or c.get('ai_readiness_notes', '')
    recs = c.get('ai_readiness_notes', '')
    tool = c.get('difficulty_level', '')
    audience = c.get('target_audience', '')
    duration = c.get('estimated_duration_minutes')

    # Before/after scores
    pipeline_html = ''
    if c.get('has_player') or c.get('player_score_before') or c.get('player_score_after'):
        pb = c.get('player_score_before', '—')
        pa = c.get('player_score_after', '—')
        pipeline_html += f'''
        <div style="background:{L};padding:8px 12px;margin:8px 0;border-left:3px solid #9C27B0;">
            <span style="font-size:7.5pt;font-weight:700;color:#9C27B0;letter-spacing:1px;">PLAYER PIPELINE</span>
            <span style="margin-left:12px;font-size:9pt;">Before: <strong>{pb}</strong> → After: <strong style="color:{score_color(pa) if isinstance(pa, (int,float)) else M};">{pa}</strong></span>
        </div>'''
    if c.get('has_video') or c.get('video_score_before') or c.get('video_score_after'):
        vb = c.get('video_score_before', '—')
        va = c.get('video_score_after', '—')
        pipeline_html += f'''
        <div style="background:{L};padding:8px 12px;margin:8px 0;border-left:3px solid #2196F3;">
            <span style="font-size:7.5pt;font-weight:700;color:#2196F3;letter-spacing:1px;">VIDEO PIPELINE</span>
            <span style="margin-left:12px;font-size:9pt;">Before: <strong>{vb}</strong> → After: <strong style="color:{score_color(va) if isinstance(va, (int,float)) else M};">{va}</strong></span>
        </div>'''

    skills_html = ''.join([
        f'<span style="display:inline-block;background:{B};color:white;padding:3px 8px;border-radius:2px;font-size:7.5pt;margin:2px 3px 2px 0;">{s}</span>'
        for s in skills[:15]
    ]) or f'<span style="color:{M};font-size:8pt;">No skills extracted</span>'

    objectives_html = ''
    if objectives:
        objectives_html = '<ul style="padding-left:16px;margin:6px 0;">' + \
            ''.join(f'<li style="font-size:8.5pt;margin-bottom:3px;">{o}</li>' for o in objectives[:8]) + \
            '</ul>'

    flags_html = ''
    if flags:
        flags_html = ''.join([
            f'<div style="padding:3px 0;font-size:8pt;color:{R};">⚠ {f}</div>'
            for f in flags[:6]
        ])
    else:
        flags_html = f'<span style="color:{G};font-size:8pt;">✓ No major quality issues detected</span>'

    meta_items = []
    if tool: meta_items.append(f'Difficulty: <strong>{tool}</strong>')
    if audience: meta_items.append(f'Audience: <strong>{audience}</strong>')
    if duration: meta_items.append(f'Duration: <strong>~{duration} min</strong>')
    meta_items.append(f'SCORM: <strong>{c.get("scorm_version","—")}</strong>')
    meta_items.append(f'Modules: <strong>{c.get("total_modules","—")}</strong>')
    meta_html = ' &nbsp;|&nbsp; '.join(meta_items)

    return f'''
    <div style="page-break-inside:avoid;border:1px solid #E0E0E0;border-top:4px solid {t_color};padding:16px;margin-bottom:20px;">
        <table style="width:100%;border:none;margin:0;">
            <tr>
                <td style="border:none;padding:0;width:70%;vertical-align:top;">
                    <div style="font-size:7pt;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:{M};margin-bottom:4px;">COURSE {idx:02d}</div>
                    <div style="font-size:14pt;font-weight:700;color:{D};line-height:1.2;margin-bottom:6px;">{title}</div>
                    <div style="font-size:7.5pt;color:{M};">{meta_html}</div>
                </td>
                <td style="border:none;padding:0;width:30%;text-align:right;vertical-align:top;">
                    {score_badge(score)}
                    <br><span style="background:{t_color};color:white;padding:3px 10px;border-radius:3px;font-size:8pt;font-weight:700;">{tier}</span>
                </td>
            </tr>
        </table>

        {pipeline_html}

        {f'<p style="font-size:8.5pt;margin:10px 0;line-height:1.5;">{summary}</p>' if summary else ''}

        <table style="width:100%;border:none;margin:10px 0 0 0;">
            <tr>
                <td style="width:48%;border:none;padding:0;vertical-align:top;">
                    <div style="font-size:7pt;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:{B};margin-bottom:4px;">Skills</div>
                    <div>{skills_html}</div>
                    {f'<div style="margin-top:10px;"><div style="font-size:7pt;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:{B};margin-bottom:4px;">Learning Objectives</div>{objectives_html}</div>' if objectives_html else ''}
                </td>
                <td style="width:4%;border:none;"></td>
                <td style="width:48%;border:none;padding:0;vertical-align:top;">
                    <div style="font-size:7pt;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:{B};margin-bottom:4px;">Content Quality Flags</div>
                    {flags_html}
                    {f'<div style="margin-top:10px;"><div style="font-size:7pt;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:{B};margin-bottom:4px;">Recommendations</div><p style="font-size:8pt;line-height:1.4;">{recs[:300]}{"..." if len(recs)>300 else ""}</p></div>' if recs else ''}
                </td>
            </tr>
        </table>
    </div>'''


def build_html(courses, client_name, date_str):
    total = len(courses)
    scores = [c['ai_readiness_score'] for c in courses if isinstance(c.get('ai_readiness_score'), (int, float))]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None
    has_llm = any(c.get('ai_readiness_score') is not None for c in courses)

    tiers = {'Good': 0, 'Needs Work': 0, 'Not Ready': 0, 'Unknown': 0}
    for c in courses:
        t = c.get('ai_readiness_tier', 'Unknown')
        if t in tiers:
            tiers[t] += 1
        else:
            tiers['Unknown'] += 1

    ready_count = tiers.get('Good', 0)
    needs_work_count = tiers.get('Needs Work', 0)
    not_ready_count = tiers.get('Not Ready', 0)
    unknown_count = tiers.get('Unknown', 0)

    ready_pct = round(100 * ready_count / total, 0) if total else 0
    not_ready_pct = round(100 * (needs_work_count + not_ready_count) / total, 0) if total else 0

    # Top quality flags
    all_flags = {}
    for c in courses:
        for f in c.get('quality_flags', []):
            all_flags[f] = all_flags.get(f, 0) + 1
    top_flags = sorted(all_flags.items(), key=lambda x: -x[1])[:5]
    top_flags_html = ''.join([f'<li style="font-size:8.5pt;margin-bottom:3px;">{f} <span style="color:{M};">({n} course{"s" if n>1 else ""})</span></li>' for f, n in top_flags])
    if not top_flags_html:
        top_flags_html = '<li style="font-size:8.5pt;">No quality issues flagged (LLM enrichment not applied)</li>'

    # Per-course sections
    course_sections = ''.join([build_course_section(c, i+1) for i, c in enumerate(courses)])

    # Appendix table
    appendix_rows = ''
    for c in courses:
        score = c.get('ai_readiness_score')
        tier = c.get('ai_readiness_tier', 'Unknown')
        t_color = tier_color(tier)
        title = (c.get('title') or c.get('file', 'Untitled'))[:40]
        skills_short = ', '.join(c.get('inferred_skills', [])[:3]) or '—'
        desc = f'<span style="color:{G};">✓</span>' if c.get('has_description') else f'<span style="color:{R};">✗</span>'
        kw = f'<span style="color:{G};">✓</span>' if c.get('has_keywords') else f'<span style="color:{R};">✗</span>'
        score_str = str(score) if score is not None else '—'
        appendix_rows += f'''<tr>
            <td><strong>{title}</strong></td>
            <td style="text-align:center;">{c.get("scorm_version","—")}</td>
            <td style="text-align:center;">{c.get("total_modules","—")}</td>
            <td style="text-align:center;">{desc}</td>
            <td style="text-align:center;">{kw}</td>
            <td style="text-align:center;font-weight:700;color:{score_color(score) if score is not None else M};">{score_str}</td>
            <td style="text-align:center;"><span style="background:{t_color};color:white;padding:2px 6px;border-radius:2px;font-size:7pt;font-weight:700;">{tier}</span></td>
            <td style="font-size:7.5pt;">{skills_short}</td>
        </tr>'''

    # Recommendations
    recs = [
        ('Immediate', 'Add descriptions to courses missing them', f'{sum(1 for c in courses if not c.get("has_description"))} courses', 'High'),
        ('Immediate', 'Add keyword tags to untagged courses', f'{sum(1 for c in courses if not c.get("has_keywords"))} courses', 'High'),
        ('30 Days', 'Map inferred skills to official taxonomy', 'Full catalog', 'High'),
        ('60 Days', 'Remediate courses scoring below 40', f'{not_ready_count} courses', 'Medium'),
        ('90 Days', 'Sunset or rebuild legacy/broken packages', 'As identified', 'Medium'),
        ('Ongoing', 'Enrich metadata on all new courses before publishing', 'Governance', 'Low'),
    ]
    rec_rows = ''
    for timeline, action, scope, priority in recs:
        p_color = R if priority == 'High' else (Y if priority == 'Medium' else G)
        rec_rows += f'<tr><td style="font-weight:700;color:{B};">{timeline}</td><td>{action}</td><td>{scope}</td><td><span style="color:{p_color};font-weight:700;">{priority}</span></td></tr>'

    avg_display = str(avg_score) if avg_score is not None else 'N/A'
    avg_color = score_color(avg_score) if avg_score is not None else M

    html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
@page {{
    size: letter;
    margin: 50px 48px 50px 48px;
    @bottom-right {{ content: counter(page); font-size: 8pt; color: #666; }}
}}
@page :first {{ margin: 0; @bottom-right {{ content: none; }} }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: Arial, Helvetica, sans-serif; font-size: 9pt; color: {D}; line-height: 1.45; }}
.cover {{ width: 8.5in; height: 11in; background: linear-gradient(155deg, {B} 0%, {B} 55%, {R} 100%);
    display: block; text-align: center; color: white; page-break-after: always; position: relative; }}
.cover-inner {{ padding-top: 2.5in; }}
.ph {{ padding-bottom: 7px; border-bottom: 1.5px solid {B}; margin-bottom: 18px; }}
.ph span {{ font-size: 7pt; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: {B}; }}
.sn {{ font-size: 7.5pt; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: {R}; margin-bottom: 6px; }}
.st {{ font-size: 18pt; font-weight: 700; color: {B}; line-height: 1.1; }}
.ss {{ font-size: 9.5pt; color: {B}; font-style: italic; margin-top: 4px; margin-bottom: 8px; }}
.sd {{ height: 3px; background: {R}; margin-bottom: 16px; width: 60px; }}
.gt {{ background: {B}; color: white; padding: 14px 20px; margin-bottom: 14px; }}
.gt p {{ font-size: 9.5pt; line-height: 1.5; font-weight: 300; }}
.stats {{ margin: 14px 0; font-size: 0; }}
.stat {{ display: inline-block; vertical-align: top; width: 22%; margin-right: 4%; text-align: center;
    padding: 14px 8px; background: {L}; border-top: 3px solid {R}; font-size: 9pt; }}
.stat:last-child {{ margin-right: 0; }}
.stat .snum {{ font-size: 26pt; font-weight: 700; color: {R}; line-height: 1; }}
.stat .slbl {{ font-size: 7pt; color: {M}; text-transform: uppercase; letter-spacing: 1px; }}
.ki {{ background: {L}; border-left: 4px solid {R}; padding: 10px 14px; margin: 12px 0; }}
.ki .kl {{ font-size: 7pt; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; color: {R}; margin-bottom: 3px; }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 8pt; }}
table th {{ background: {B}; color: white; padding: 7px 10px; text-align: left; font-weight: 700;
    font-size: 7pt; letter-spacing: 1px; text-transform: uppercase; }}
table td {{ padding: 6px 10px; border-bottom: 1px solid #E0E0E0; vertical-align: top; line-height: 1.35; }}
table tr:nth-child(even) td {{ background: {L}; }}
.divstrip {{ background: {B}; color: white; padding: 8px 16px; margin: 14px 0; font-size: 7.5pt; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; }}
</style>
</head>
<body>

<!-- COVER -->
<div class="cover">
  <div class="cover-inner">
    <div style="font-size:8pt;font-weight:700;letter-spacing:4px;text-transform:uppercase;opacity:0.7;margin-bottom:20px;">Confidential Assessment</div>
    <div style="font-size:36pt;font-weight:700;line-height:1.1;margin-bottom:12px;">SCORM AI Readiness<br>Assessment Report</div>
    <div style="font-size:14pt;font-weight:300;opacity:0.85;margin-bottom:40px;">Course Catalog Audit &amp; Remediation Roadmap</div>
    <div style="width:60px;height:3px;background:white;margin:0 auto 30px;opacity:0.5;"></div>
    <div style="font-size:11pt;font-weight:700;letter-spacing:2px;text-transform:uppercase;opacity:0.9;">Prepared for: {client_name}</div>
    <div style="font-size:8pt;opacity:0.6;margin-top:8px;">Assessment Date: {date_str} &nbsp;|&nbsp; {total} Package{"s" if total != 1 else ""} Analyzed</div>
  </div>
  <div style="position:absolute;bottom:0;left:0;right:0;height:6px;background:rgba(255,255,255,0.3);"></div>
</div>

<!-- EXECUTIVE SUMMARY -->
<div class="ph"><span>SCORM AI Readiness Report • Executive Summary</span></div>
<div class="sn">SECTION 01</div>
<div class="st">Executive Summary</div>
<div class="ss">Key findings from your SCORM catalog AI readiness audit.</div>
<div class="sd"></div>

<div class="gt"><p>This assessment evaluated {total} SCORM package{"s" if total!=1 else ""} from your LMS catalog against AI personalization readiness criteria.
The findings reveal the current state of metadata quality, skills tagging, and content structure —
the core inputs that determine whether your LMS can deliver AI-powered learning recommendations.</p></div>

<div class="stats">
    <div class="stat"><div class="snum">{total}</div><div class="slbl">Packages Audited</div></div>
    <div class="stat">
        <div class="snum" style="font-size:26pt;color:{avg_color};">{avg_display}</div>
        <div class="slbl">Avg Readiness Score</div>
    </div>
    <div class="stat"><div class="snum">{ready_pct:.0f}%</div><div class="slbl">Good / Ready</div></div>
    <div class="stat"><div class="snum">{not_ready_pct:.0f}%</div><div class="slbl">Needs Remediation</div></div>
</div>

<div class="divstrip">Score Distribution</div>
<table>
    <tr><th>Tier</th><th>Score Range</th><th>Count</th><th>What It Means</th></tr>
    <tr><td><span style="color:{G};font-weight:700;">Good / AI Ready</span></td><td>70–100</td><td><strong>{ready_count}</strong></td><td>Ready for AI recommendations.</td></tr>
    <tr><td><span style="color:{Y};font-weight:700;">Needs Work</span></td><td>40–69</td><td><strong>{needs_work_count}</strong></td><td>Significant cleanup needed before AI use.</td></tr>
    <tr><td><span style="color:{R};font-weight:700;">Not Ready</span></td><td>0–39</td><td><strong>{not_ready_count}</strong></td><td>Major metadata gaps. AI personalization will fail.</td></tr>
    {'<tr><td><span style="color:'+M+';font-weight:700;">Unknown</span></td><td>N/A</td><td><strong>'+str(unknown_count)+'</strong></td><td>No AI enrichment applied.</td></tr>' if unknown_count else ''}
</table>

<div class="divstrip">Top Quality Issues</div>
<ul style="padding-left:20px;margin:8px 0;">{top_flags_html}</ul>

<div class="ki">
    <div class="kl">Bottom Line</div>
    <p style="font-size:9pt;">{
    f"Only {ready_count} of {total} courses ({int(ready_pct)}%) are ready for AI personalization today. The remaining {needs_work_count + not_ready_count} courses require metadata remediation before they can be surfaced effectively by recommendation engines."
    if has_llm else
    "LLM enrichment was not applied. Run with --llm flag for AI readiness scores."
    }</p>
</div>

<!-- PAGE BREAK -->
<div style="page-break-before: always;"></div>

<!-- PER-COURSE SECTIONS -->
<div class="ph"><span>SCORM AI Readiness Report • Course Detail</span></div>
<div class="sn">SECTION 02</div>
<div class="st">Course Detail</div>
<div class="ss">Per-course breakdown with AI readiness scoring and quality analysis.</div>
<div class="sd"></div>

{course_sections}

<!-- PAGE BREAK -->
<div style="page-break-before: always;"></div>

<!-- RECOMMENDATIONS -->
<div class="ph"><span>SCORM AI Readiness Report • Remediation Roadmap</span></div>
<div class="sn">SECTION 03</div>
<div class="st">Remediation Roadmap</div>
<div class="ss">Prioritized actions to bring your catalog to AI-ready status.</div>
<div class="sd"></div>

<div class="gt"><p>Achieving AI-powered personalized learning requires systematic metadata enrichment.
The roadmap below prioritizes actions by impact and timeline, moving your catalog from its current state
to full AI readiness over a 90-day remediation cycle.</p></div>

<table>
    <tr><th>Timeline</th><th>Action</th><th>Scope</th><th>Priority</th></tr>
    {rec_rows}
</table>

<div class="ki" style="margin-top:16px;">
    <div class="kl">The AI Personalization Stack</div>
    <p style="font-size:9pt;">Metadata quality → Skills taxonomy → AI recommendations → Personalized learner experience.
    Each layer depends on the one below it. Skipping metadata cleanup means your AI engine will recommend
    the wrong content to the wrong people — or nothing at all.</p>
</div>

<!-- PAGE BREAK -->
<div style="page-break-before: always;"></div>

<!-- APPENDIX -->
<div class="ph"><span>SCORM AI Readiness Report • Appendix</span></div>
<div class="sn">APPENDIX</div>
<div class="st">Raw Data Table</div>
<div class="ss">All analyzed courses with full metadata summary.</div>
<div class="sd"></div>

<table style="font-size:7.5pt;">
    <tr>
        <th>Course Title</th><th>SCORM</th><th>Modules</th>
        <th>Desc</th><th>KW</th><th>Score</th><th>Tier</th><th>Top Skills</th>
    </tr>
    {appendix_rows}
</table>

<div style="margin-top:20px;padding:14px;border:1px solid #E0E0E0;border-top:3px solid {B};">
    <p style="font-size:8pt;font-weight:700;color:{B};margin-bottom:8px;">About This Assessment</p>
    <p style="font-size:8pt;color:{M};">Generated by SCORM Analyzer (Claw 🦊) on {date_str}.
    AI readiness scores reflect metadata completeness, skills tagging coverage, and content structure quality.
    {"AI enrichment was applied to infer skills and generate quality flags." if has_llm else "Scores are based on structural metadata only (no AI enrichment applied)."}
    Remediation priorities are recommendations only.</p>
</div>

</body>
</html>'''
    return html


def main():
    parser = argparse.ArgumentParser(description='Generate consulting-grade PDF from SCORM analysis')
    parser.add_argument('input', nargs='?', help='JSON file or folder of JSON files (default: scorm_inbox/*.json)')
    parser.add_argument('--output', help='Output PDF path (default: output/scorm_report_YYYY-MM-DD.pdf)')
    parser.add_argument('--client', default='Client Organization', help='Client name for cover page')
    args = parser.parse_args()

    date_str = datetime.utcnow().strftime('%Y-%m-%d')

    # Determine input
    if args.input:
        input_path = Path(args.input)
    else:
        # Default: scorm_inbox/*.json
        inbox = WORKSPACE / 'scorm_inbox'
        if not inbox.exists() or not list(inbox.glob('*.json')):
            # Try output/batch dirs
            batch_dirs = sorted((WORKSPACE / 'output').glob('batch_*'))
            if batch_dirs:
                input_path = batch_dirs[-1]
                print(f"📂 Using latest batch: {input_path}")
            else:
                print("❌ No input specified and no JSON files found in scorm_inbox/")
                sys.exit(1)
        else:
            input_path = inbox

    print(f"📄 Loading data from: {input_path}")
    courses, raw_results = load_course_data(input_path)

    if not courses:
        print("❌ No course data found")
        sys.exit(1)

    print(f"📊 Found {len(courses)} course(s)")

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f'scorm_report_{date_str}.pdf'

    print(f"🎨 Building report for: {args.client}")
    html = build_html(courses, args.client, date_str)

    print(f"📑 Generating PDF...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html).write_pdf(str(output_path))

    print(f"✅ Report saved: {output_path}")
    return str(output_path)


if __name__ == '__main__':
    main()
