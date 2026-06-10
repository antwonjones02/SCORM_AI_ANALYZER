# SCORM AI Analyzer

**Built by Antwon Jones | AI & L&D**

The one-stop SCORM extractor: drop in a course package and get back **every data
point it contains** — metadata, structure, skills, duration, category, quiz
questions, slide content — plus an **AI-readiness score**, slide screenshots from
an automated playthrough, and a consulting-grade report.

Enterprise L&D teams want AI-powered personalization, but AI can't read SCORM
content: courses ship with no descriptions, no keywords, no objectives. They're
black boxes. **This tool opens them.**

---

## What it does

```
course.zip ──► EXTRACT ──► IDENTIFY ──► PARSE ──► MINE ──► SCORE ──► PLAY ──► REPORT
               zip-slip    SCORM 1.2     full      slide    0-100     headless   JSON
               safe        SCORM 2004    LOM       text     rubric    browser    HTML
                           xAPI/cmi5     manifest  quizzes  with      passes     PDF
                           AICC          deep      captions breakdown the        XLSX
                                         parse                        course
```

1. **Extract** — safe unzip (zip-slip protected), SHA-256, full file inventory
2. **Identify** — standard (SCORM 1.2 / 2004 / xAPI / cmi5 / AICC) + authoring
   tool fingerprinting (Storyline, Rise, Captivate, iSpring, Lectora, Camtasia,
   Adapt, Evolve, Gomo, Elucidat, …)
3. **Parse** — deep manifest/LOM extraction: title, description, keywords,
   duration (normalized to minutes), language, author/contributors, copyright,
   classification taxonomy → category, difficulty, intended audience, mastery
   scores, objectives, prerequisites, full structure tree, external metadata files
4. **Mine** — deterministic content extraction, **no AI key needed**: HTML text,
   Storyline `globalProvideData` blobs, Rise embedded course JSON, sentence-like
   JS strings (SPA courses), captions (.vtt/.srt), generic JSON content —
   yielding slide titles, body text, and quiz questions
5. **Score** — deterministic AI-readiness rubric (0-100) with an itemized,
   color-coded breakdown of exactly what's missing
6. **Play** *(optional)* — headless Chromium runs the course **like a learner**:
   SCORM API shim, click-through navigation, scroll handling for Rise, quiz
   auto-answering (LLM-picked answers when a key is set, with wrong-answer
   retry), drag-drop resolution — and captures the full LMS runtime: completion
   status, score, session time, every `cmi.*` write, every interaction
7. **Report** — self-contained HTML report (screenshots embedded), JSON with
   every data point, plus legacy PDF/XLSX generators

Everything except the optional player/LLM/video pipelines runs on the **Python
standard library alone**.

---

## Quickstart

```bash
git clone https://github.com/antwonjones02/SCORM_AI_ANALYZER.git
cd SCORM_AI_ANALYZER

# Core extraction needs NO dependencies:
python3 scormshop.py analyze course.zip

# For the browser player pipeline:
pip install playwright && playwright install chromium
python3 scormshop.py analyze course.zip --play

# For AI enrichment (skills, summaries, smart quiz answers):
pip install anthropic
export ANTHROPIC_API_KEY="sk-ant-..."
python3 scormshop.py analyze course.zip --play --llm
```

Outputs land in `output/`:

| File | Contents |
|------|----------|
| `output/<name>.json` | Every extracted data point (schema below) |
| `output/report.html` | Self-contained report — open in any browser |
| `output/screenshots/<name>/` | Slide screenshots from the playthrough |

---

## The one-stop CLI: `scormshop.py`

```bash
# Analyze one package or a whole folder
python3 scormshop.py analyze course.zip
python3 scormshop.py analyze scorm_inbox/ --play --llm --pdf

# Player only: run the course, capture SCORM runtime data
python3 scormshop.py play course.zip

# Rebuild the HTML report from saved JSON results
python3 scormshop.py report output/*.json --title "Q3 Catalog Audit"
```

Key flags for `analyze`:

| Flag | Effect |
|------|--------|
| `--play` | Play the course in headless Chromium, capture runtime + screenshots |
| `--llm` | AI enrichment: skills, audience, category, summary (needs API key) |
| `--video` | Transcribe MP4s with Whisper, re-score with transcript |
| `--llm-provider` | `claude` (default) or `deepseek` |
| `--pdf` | Also render the report as PDF (needs WeasyPrint) |
| `--out DIR` | Output directory (default `output/`) |
| `--max-slides N` | Player navigation budget (default 40) |

Legacy entry points still work: `scorm_analyzer.py` (single course CLI),
`batch_analyzer.py` (folder + PDF), `generate_report.py` / `generate_xlsx.py` /
`generate_playbook.py` (consulting deliverables).

---

## AI-Readiness Score (0-100)

Deterministic rubric — every package gets a score with zero API calls, and the
report shows the per-criterion breakdown:

| Criterion | Max | What it checks |
|-----------|-----|----------------|
| Title | 10 | Present and non-generic |
| Description | 15 | Rich (≥80 chars) vs short vs missing |
| Keywords | 10 | ≥3 skills/keyword tags |
| Duration | 5 | Machine-readable duration metadata |
| Objectives | 10 | Learning objectives declared in manifest |
| Structure | 10 | Multiple titled modules/items |
| Extractable text | 15 | Words of minable content (500+ = full marks) |
| Assessment | 10 | Quiz questions detected / mastery score set |
| Language | 5 | Language metadata |
| Categorization | 5 | Classification taxonomy present |
| Provenance | 5 | Author/contributor metadata |

| Score | Tier | Meaning |
|-------|------|---------|
| 70–100 | 🟢 AI Ready | Well-documented; AI can index, search and recommend it |
| 40–69 | 🟡 Partially Ready | Some content extractable; needs enrichment |
| 0–39 | 🔴 Not Ready | Black box — AI personalization will fail |

When `--llm` or `--play` (with vision) runs, you also get a before/after score
showing how much value enrichment unlocks.

---

## What the player captures

The headless player injects a full SCORM 1.2 + 2004 API shim and walks the
course like a learner. From the JSON:

```json
"player_pipeline": {
  "course_type": "storyline",
  "completed_naturally": true,
  "runtime": {
    "completion_status": "completed",
    "success_status": "passed",
    "score_raw": 100.0,
    "session_time": "0000:05:30",
    "interactions": {
      "0": {"id": "q1-pass-technique", "type": "choice",
             "student_response": "b", "result": "correct"}
    },
    "api_call_count": 23,
    "lms_initialized": true,
    "lms_finish_called": true,
    "cmi_data": { "...every cmi.* element the course wrote..." }
  },
  "slides": [
    {"index": 4, "interaction_type": "quiz", "quiz_answered": 1,
     "quiz_solver": "llm", "screenshot_path": "output/screenshots/.../slide_004.png"}
  ]
}
```

Quiz strategy: with an `ANTHROPIC_API_KEY` set, an LLM reads each question and
picks the best answer; without one, it answers deterministically and rotates
options when the course offers a retry. Locked slides get progressively heavier
unlock strategies (quiz submit → simulated drag-drop → click-everything).

---

## Full JSON schema (per course)

```json
{
  "analyzer_version": "2.0",
  "source_file": "course.zip", "sha256": "...", "file_size_bytes": 123,
  "package_type": "scorm | xapi | cmi5 | aicc",
  "scorm_version": "1.2 | 2004 (3rd Edition) | xAPI (Tin Can) | cmi5 | AICC",
  "authoring_tool": {"name": "Articulate Rise", "confidence": "high", "evidence": "..."},
  "metadata": {
    "title": "...", "description": "...", "keywords": [], "language": "en",
    "duration": "PT45M", "duration_minutes": 45.0, "author": "...",
    "contributors": [], "copyright": "...", "categories": [],
    "classifications": [], "difficulty": "...", "educational_contexts": [],
    "intended_end_user_roles": [], "identifier": "...", "version": "..."
  },
  "organizations": [{"title": "...", "items": [{"title": "...", "mastery_score": "80",
                     "objectives": [], "prerequisites": "", "children": []}]}],
  "resources": [{"id": "...", "scorm_type": "sco", "href": "index.html",
                 "files": [], "dependencies": []}],
  "files": {"total_files": 4, "by_extension": {}, "media": {"videos": [],
            "captions": []}, "entry_point": "index.html"},
  "content": {"slide_titles": [], "text": "...", "word_count": 373,
              "quiz_questions": [], "captions": {}, "sources": ["js_strings"]},
  "stats": {"ai_readiness_score": 77, "ai_readiness_tier": "AI Ready",
            "score_breakdown": {"title": {"points": 10, "max": 10, "note": "..."}}},
  "llm_analysis": {"ai_summary": "...", "inferred_skills": [], "category": "...",
                   "target_audience": "...", "learning_objectives": []},
  "player_pipeline": {"...": "see above"},
  "video_pipeline": {"transcripts": {}, "score_before": 28, "score_after": 62}
}
```

---

## Project layout

| Path | Purpose |
|------|---------|
| `scormshop.py` | **The one-stop CLI** — analyze / play / report |
| `scormlib/manifest.py` | Deep SCORM 1.2/2004 + LOM manifest parser |
| `scormlib/package.py` | Safe extraction, standard + tool detection, inventory |
| `scormlib/content.py` | Deterministic content mining (no AI) |
| `scormlib/scoring.py` | AI-readiness rubric with breakdown |
| `scormlib/player.py` | Headless browser player + SCORM API shim |
| `scormlib/llm.py` | Optional Claude/DeepSeek enrichment + quiz solving |
| `scormlib/analyzer.py` | Pipeline orchestrator |
| `scormlib/report_html.py` | Self-contained HTML report |
| `scorm_analyzer.py` | Legacy-compatible single-course CLI |
| `batch_analyzer.py` | Folder batch + PDF summary |
| `generate_report.py` / `generate_xlsx.py` / `generate_playbook.py` | Consulting deliverables (PDF/XLSX) |
| `tests/` | 50-test suite incl. real-browser integration tests |
| `tests/fixtures/` | Realistic sample packages (SCORM 1.2/2004, Rise-like, xAPI) |

## Running tests

```bash
pip install pytest playwright && playwright install chromium
pytest tests/ -v          # browser tests auto-skip if Chromium is unavailable
```

## Requirements

- Python 3.9+ (core: stdlib only)
- Optional: Playwright + Chromium (`--play`), Anthropic/DeepSeek key (`--llm`),
  ffmpeg + openai-whisper (`--video`), WeasyPrint (`--pdf`), openpyxl (XLSX)

If Playwright's browser download is blocked in your environment, point the
player at any Chrome/Chromium binary: `export SCORM_BROWSER_PATH=/path/to/chrome`.
