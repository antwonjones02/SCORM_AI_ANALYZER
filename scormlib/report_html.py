"""Self-contained HTML report generator — zero dependencies, opens anywhere.

Renders every extracted data point: metadata, score breakdown, structure,
mined content, player runtime results (with screenshot gallery), file
inventory, and LLM analysis when present."""

import base64
import html
from datetime import datetime, timezone
from pathlib import Path

from .scoring import score_to_tier

_TIER_COLORS = {'AI Ready': '#1a7f37', 'Partially Ready': '#b08800',
                'Not Ready': '#c8102e', 'Unknown': '#666666'}


def esc(value):
    return html.escape(str(value)) if value is not None else ''


def _score_color(score):
    if score is None:
        return '#666'
    if score >= 70:
        return '#1a7f37'
    if score >= 40:
        return '#b08800'
    return '#c8102e'


def _kv_rows(pairs):
    rows = []
    for label, value in pairs:
        if value in (None, '', [], {}):
            value = '<span class="missing">— missing —</span>'
        elif isinstance(value, list):
            value = ', '.join(esc(v) for v in value)
        else:
            value = esc(value)
        rows.append(f'<tr><th>{esc(label)}</th><td>{value}</td></tr>')
    return '\n'.join(rows)


def _badge(text, color='#003087'):
    return (f'<span style="background:{color};color:#fff;padding:2px 10px;'
            f'border-radius:10px;font-size:12px;font-weight:600;">{esc(text)}</span>')


def _items_tree(items, depth=0):
    if not items:
        return ''
    out = ['<ul class="tree">']
    for item in items:
        title = esc(item.get('title') or item.get('id') or '(untitled)')
        extras = []
        if item.get('mastery_score'):
            extras.append(f'mastery {esc(item["mastery_score"])}')
        if item.get('objectives'):
            extras.append(f'{len(item["objectives"])} objective(s)')
        suffix = f' <span class="dim">({", ".join(extras)})</span>' if extras else ''
        out.append(f'<li>{title}{suffix}{_items_tree(item.get("children", []), depth + 1)}</li>')
    out.append('</ul>')
    return ''.join(out)


def _embed_image(path, max_bytes=400_000):
    try:
        data = Path(path).read_bytes()
        if len(data) > max_bytes:
            return None
        return base64.standard_b64encode(data).decode()
    except OSError:
        return None


def _course_section(result, idx, embed_screenshots=True):
    meta = result.get('metadata', {})
    stats = result.get('stats', {})
    content = result.get('content', {})
    files = result.get('files', {})
    tool = result.get('authoring_tool', {})
    player = result.get('player_pipeline') or {}
    llm = result.get('llm_analysis') or {}
    video = result.get('video_pipeline') or {}

    score = stats.get('ai_readiness_score')
    tier = stats.get('ai_readiness_tier') or score_to_tier(score)
    title = meta.get('title') or Path(result.get('source_file', 'Untitled')).stem

    if result.get('error'):
        return f'''<section class="course" id="course{idx}">
        <h2>{idx:02d}. {esc(title)}</h2>
        <div class="error">⚠ {esc(result["error"])}</div></section>'''

    # ── metadata table ──
    meta_pairs = [
        ('Title', meta.get('title')),
        ('Description', meta.get('description')),
        ('Keywords / skills tags', meta.get('keywords')),
        ('Duration (declared)',
         f"{meta.get('duration_minutes')} min" if meta.get('duration_minutes')
         else meta.get('duration')),
        ('Language', meta.get('language')),
        ('Author', meta.get('author')),
        ('Version', meta.get('version')),
        ('Copyright / rights', meta.get('copyright')),
        ('Category / taxonomy', meta.get('categories')),
        ('Difficulty (LOM)', meta.get('difficulty')),
        ('Educational context', meta.get('educational_contexts')),
        ('Intended roles', meta.get('intended_end_user_roles')),
        ('Resource types', meta.get('learning_resource_types')),
        ('Identifier', meta.get('identifier')),
        ('Standard', f"{result.get('package_type', '?').upper()} "
                     f"{result.get('scorm_version', '')}"),
        ('Authoring tool', f"{tool.get('name', 'unknown')}"
                           + (f" ({tool.get('evidence')})" if tool.get('evidence') else '')),
        ('Package size', f"{(result.get('file_size_bytes') or 0) / 1024 / 1024:.1f} MB "
                         f"zip / {files.get('total_files', '?')} files"),
        ('SHA-256', (result.get('sha256') or '')[:16] + '…'),
    ]

    # ── score breakdown ──
    breakdown_rows = ''
    for name, b in (stats.get('score_breakdown') or {}).items():
        pct = b['points'] / b['max'] if b['max'] else 0
        color = '#1a7f37' if pct >= 0.99 else ('#b08800' if pct > 0 else '#c8102e')
        breakdown_rows += (
            f'<tr><td>{esc(name.replace("_", " ").title())}</td>'
            f'<td style="text-align:center;color:{color};font-weight:700;">'
            f'{b["points"]}/{b["max"]}</td><td>{esc(b["note"])}</td></tr>')

    # ── structure ──
    structure_html = ''
    for org in result.get('organizations', []):
        structure_html += (f'<p><strong>{esc(org.get("title") or org.get("id"))}'
                           f'</strong></p>{_items_tree(org.get("items", []))}')
    if not structure_html:
        structure_html = '<p class="missing">No organization structure declared.</p>'

    # ── content insights ──
    slide_titles = content.get('slide_titles', [])
    quiz_qs = content.get('quiz_questions', [])
    content_html = f'''
      <p>{_badge(f"{content.get('word_count', 0):,} words extracted", '#003087')}
         {_badge(f"{len(slide_titles)} slide/section titles", '#5a32a3')}
         {_badge(f"{len(quiz_qs)} quiz questions", '#9a3412')}
         {_badge('sources: ' + (', '.join(content.get('sources', [])) or 'none'), '#444')}</p>'''
    if slide_titles:
        content_html += ('<details open><summary>Slide / section titles</summary><ol>'
                         + ''.join(f'<li>{esc(t)}</li>' for t in slide_titles[:40])
                         + '</ol></details>')
    if quiz_qs:
        content_html += ('<details open><summary>Quiz questions detected</summary><ul>'
                         + ''.join(f'<li>{esc(q)}</li>' for q in quiz_qs[:25])
                         + '</ul></details>')
    if content.get('text'):
        content_html += (f'<details><summary>Extracted text sample</summary>'
                         f'<pre class="sample">{esc(content["text"][:3000])}</pre></details>')

    # ── player results ──
    player_html = ''
    if player:
        if player.get('error'):
            player_html = f'<div class="error">Player error: {esc(player["error"])}</div>'
        else:
            rt = player.get('runtime', {})
            completion = rt.get('completion_status', 'unknown')
            comp_color = '#1a7f37' if completion in ('completed', 'passed') else '#c8102e'
            interactions_rows = ''
            for iid, idata in (rt.get('interactions') or {}).items():
                interactions_rows += (
                    f'<tr><td>{esc(idata.get("id", iid))}</td>'
                    f'<td>{esc(idata.get("type", ""))}</td>'
                    f'<td>{esc(idata.get("student_response") or idata.get("learner_response", ""))}</td>'
                    f'<td>{esc(idata.get("result", ""))}</td></tr>')
            player_html = f'''
            <p>{_badge(f"course type: {player.get('course_type')}", '#444')}
               {_badge(f"completion: {completion}", comp_color)}
               {_badge(f"score: {rt.get('score_raw') if rt.get('score_raw') is not None else '—'}", '#003087')}
               {_badge(f"session time: {rt.get('session_time') or '—'}", '#444')}
               {_badge(f"{rt.get('api_call_count', 0)} LMS API calls", '#5a32a3')}
               {_badge('forced completion' if player.get('forced_completion') else 'completed by navigation', '#9a3412' if player.get('forced_completion') else '#1a7f37')}</p>
            <p class="dim">Slides visited: {player.get('total_slides_detected', 0)} ·
               screenshots: {player.get('screenshots_captured', 0)} ·
               stalls: {player.get('stall_count', 0)} ·
               LMS initialized: {rt.get('lms_initialized')} ·
               Finish/Terminate called: {rt.get('lms_finish_called')}</p>'''
            if interactions_rows:
                player_html += ('<table><tr><th>Interaction</th><th>Type</th>'
                                '<th>Learner response</th><th>Result</th></tr>'
                                + interactions_rows + '</table>')
            if embed_screenshots and player.get('slides'):
                shots = ''
                for slide in player['slides']:
                    sp = slide.get('screenshot_path')
                    if not sp:
                        continue
                    b64 = _embed_image(sp)
                    if not b64:
                        continue
                    cap = (f"#{slide.get('index')} · {slide.get('interaction_type')}"
                           + (' · stalled' if slide.get('stalled') else ''))
                    shots += (f'<figure><img src="data:image/png;base64,{b64}" '
                              f'loading="lazy"/><figcaption>{esc(cap)}</figcaption></figure>')
                if shots:
                    player_html += f'<div class="gallery">{shots}</div>'

    # ── LLM analysis ──
    llm_html = ''
    if llm and not llm.get('error'):
        skills = ''.join(_badge(s, '#003087') + ' '
                         for s in llm.get('inferred_skills', [])[:15])
        llm_pairs = [
            ('AI summary', llm.get('ai_summary')),
            ('Target audience', llm.get('target_audience')),
            ('Difficulty', llm.get('difficulty_level')),
            ('Category', llm.get('category')),
            ('Estimated duration', f"{llm.get('estimated_duration_minutes')} min"
             if llm.get('estimated_duration_minutes') else None),
            ('Learning objectives', llm.get('learning_objectives')),
            ('Quality flags', llm.get('content_quality_flags')),
            ('AI readiness notes', llm.get('ai_readiness_notes')),
        ]
        llm_score = llm.get('ai_readiness_score')
        llm_html = f'''<h3>AI enrichment</h3>
        <p>LLM readiness score:
           <strong style="color:{_score_color(llm_score)};">{esc(llm_score)}</strong>/100</p>
        <p>{skills}</p><table class="kv">{_kv_rows(llm_pairs)}</table>'''
    elif llm.get('error'):
        llm_html = f'<h3>AI enrichment</h3><p class="dim">{esc(llm["error"])}</p>'

    # ── video pipeline ──
    video_html = ''
    if video and video.get('transcripts'):
        video_html = '<h3>Video transcripts</h3>'
        for fname, text in list(video['transcripts'].items())[:10]:
            video_html += (f'<details><summary>{esc(fname)}</summary>'
                           f'<pre class="sample">{esc(text[:2500])}</pre></details>')

    # ── file inventory ──
    media = files.get('media', {})
    inv_pairs = [
        ('Total files', f"{files.get('total_files', 0)} "
                        f"({(files.get('total_uncompressed_bytes') or 0) / 1024 / 1024:.1f} MB uncompressed)"),
        ('Entry point', files.get('entry_point')),
        ('Videos', [v['path'] for v in media.get('videos', [])[:10]] or None),
        ('Audio files', [a['path'] for a in media.get('audios', [])[:10]] or None),
        ('Images', media.get('images_count')),
        ('Caption files', media.get('captions') or None),
        ('Documents', media.get('documents') or None),
    ]

    return f'''
    <section class="course" id="course{idx}">
      <div class="course-head">
        <div>
          <div class="eyebrow">COURSE {idx:02d} · {esc(Path(result.get("source_file", "")).name)}</div>
          <h2>{esc(title)}</h2>
        </div>
        <div class="scorebox">
          <div class="num" style="color:{_score_color(score)};">{score if score is not None else "—"}</div>
          <div class="tier" style="background:{_TIER_COLORS.get(tier, "#666")};">{esc(tier)}</div>
        </div>
      </div>
      <h3>Metadata — every declared data point</h3>
      <table class="kv">{_kv_rows(meta_pairs)}</table>
      <h3>AI-readiness score breakdown (deterministic)</h3>
      <table><tr><th>Criterion</th><th>Points</th><th>Finding</th></tr>{breakdown_rows}</table>
      <h3>Course structure</h3>{structure_html}
      <h3>Mined content (no AI required)</h3>{content_html}
      {'<h3>Player run — course played like a learner</h3>' + player_html if player_html else ''}
      {llm_html}
      {video_html}
      <h3>File inventory</h3>
      <table class="kv">{_kv_rows(inv_pairs)}</table>
    </section>'''


def build_html_report(results, report_title='SCORM Extraction Report',
                      embed_screenshots=True):
    """Build a single self-contained HTML document from analyzer result dicts."""
    if isinstance(results, dict):
        results = [results]

    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    scores = [r.get('stats', {}).get('ai_readiness_score') for r in results
              if r.get('stats', {}).get('ai_readiness_score') is not None]
    avg = round(sum(scores) / len(scores), 1) if scores else None

    summary_rows = ''
    for i, r in enumerate(results, 1):
        meta = r.get('metadata', {})
        stats = r.get('stats', {})
        score = stats.get('ai_readiness_score')
        tier = stats.get('ai_readiness_tier', 'Unknown')
        rt = (r.get('player_pipeline') or {}).get('runtime', {})
        summary_rows += f'''<tr>
          <td><a href="#course{i}">{esc(meta.get("title") or Path(r.get("source_file", "?")).name)}</a></td>
          <td>{esc(r.get("package_type", "?"))} {esc(r.get("scorm_version", ""))}</td>
          <td>{esc(r.get("authoring_tool", {}).get("name", "?"))}</td>
          <td style="text-align:center;font-weight:700;color:{_score_color(score)};">{score if score is not None else "—"}</td>
          <td><span class="tierpill" style="background:{_TIER_COLORS.get(tier, "#666")};">{esc(tier)}</span></td>
          <td>{esc(rt.get("completion_status", "—"))}</td>
          <td style="text-align:center;">{esc(stats.get("word_count", 0))}</td>
        </tr>'''

    sections = ''.join(_course_section(r, i, embed_screenshots)
                       for i, r in enumerate(results, 1))

    return f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(report_title)}</title>
<style>
:root {{ --navy:#003087; --red:#c8102e; }}
* {{ box-sizing:border-box; }}
body {{ font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif; margin:0;
       color:#23272d; background:#f2f4f8; line-height:1.5; }}
header {{ background:linear-gradient(135deg,var(--navy),#1a4fa3 70%,var(--red));
         color:#fff; padding:38px 6vw 30px; }}
header h1 {{ margin:0 0 6px; font-size:30px; }}
header .sub {{ opacity:.85; font-size:14px; }}
main {{ padding:24px 6vw 60px; max-width:1200px; margin:0 auto; }}
section.course {{ background:#fff; border-radius:10px; padding:26px 30px;
                  margin:26px 0; box-shadow:0 1px 4px rgba(0,0,0,.08); }}
.course-head {{ display:flex; justify-content:space-between; align-items:flex-start;
               gap:18px; border-bottom:2px solid #eef1f6; padding-bottom:12px; }}
.eyebrow {{ font-size:11px; letter-spacing:2px; color:#888; font-weight:700; }}
h2 {{ margin:4px 0 0; color:var(--navy); font-size:24px; }}
h3 {{ margin:26px 0 8px; color:var(--navy); font-size:15px; text-transform:uppercase;
     letter-spacing:1px; border-left:4px solid var(--red); padding-left:8px; }}
.scorebox {{ text-align:center; min-width:90px; }}
.scorebox .num {{ font-size:44px; font-weight:800; line-height:1; }}
.scorebox .tier {{ color:#fff; border-radius:12px; padding:2px 10px; font-size:12px;
                  font-weight:700; margin-top:6px; display:inline-block; }}
table {{ border-collapse:collapse; width:100%; font-size:13.5px; margin:8px 0; }}
th, td {{ text-align:left; padding:7px 10px; border-bottom:1px solid #e8ebf1;
         vertical-align:top; }}
table.kv th {{ width:220px; color:#555; font-weight:600; background:#f7f9fc; }}
tr th {{ background:#f0f3f9; }}
.missing {{ color:#b34040; font-style:italic; }}
.dim {{ color:#777; font-size:12.5px; }}
.error {{ background:#fdecec; border-left:4px solid var(--red); padding:10px 14px;
         border-radius:4px; }}
ul.tree {{ margin:4px 0 4px 18px; padding:0; }}
ul.tree li {{ margin:3px 0; }}
pre.sample {{ background:#f7f9fc; padding:12px; border-radius:6px; white-space:pre-wrap;
             font-size:12px; max-height:300px; overflow:auto; }}
.gallery {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr));
           gap:14px; margin-top:12px; }}
.gallery figure {{ margin:0; }}
.gallery img {{ width:100%; border:1px solid #dde2ea; border-radius:6px; }}
.gallery figcaption {{ font-size:11.5px; color:#666; margin-top:3px; }}
details summary {{ cursor:pointer; font-weight:600; color:var(--navy); margin:8px 0 4px; }}
.tierpill {{ color:#fff; border-radius:10px; padding:2px 9px; font-size:11.5px;
            font-weight:700; white-space:nowrap; }}
footer {{ text-align:center; color:#888; font-size:12px; padding:18px; }}
</style></head>
<body>
<header>
  <h1>{esc(report_title)}</h1>
  <div class="sub">{len(results)} package(s) analyzed · generated {date_str}
  {f" · average AI-readiness score: <strong>{avg}</strong>/100" if avg is not None else ""}</div>
</header>
<main>
  <section class="course">
    <h2 style="font-size:18px;">Catalog summary</h2>
    <table><tr><th>Course</th><th>Standard</th><th>Authoring tool</th><th>Score</th>
    <th>Tier</th><th>Player completion</th><th>Words mined</th></tr>{summary_rows}</table>
  </section>
  {sections}
</main>
<footer>Generated by SCORM AI Analyzer v2 — deterministic extraction + optional AI enrichment</footer>
</body></html>'''


def write_html_report(results, output_path, report_title='SCORM Extraction Report',
                      embed_screenshots=True):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_html_report(results, report_title,
                                             embed_screenshots),
                           encoding='utf-8')
    return output_path
