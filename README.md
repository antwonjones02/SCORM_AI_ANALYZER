# SCORM AI Readiness Analyzer 

**Built by Antwon Jones | AI & L&D**

A professional-grade toolkit that analyzes SCORM course packages and produces AI Readiness scores, extracted skills, learning objectives, and consulting-ready PDF reports.

---

## What It Does

Enterprise L&D teams are trying to add AI-powered personalization to their learning platforms — but AI can't read SCORM content. Courses have no descriptions, no keywords, no objectives. They're black boxes.

This tool opens those black boxes.

### Pipeline Overview

```
SCORM ZIP
    │
    ▼
┌─────────────────┐
│  Parser         │  Extracts manifest, metadata, structure
│  (scorm_analyzer)│  Baseline AI Readiness Score (0-100)
└────────┬────────┘
         │
         ├── Has MP4? ──► Video Pipeline
         │                  ffmpeg → audio extraction
         │                  Whisper → speech-to-text transcript
         │                  Claude → re-score with transcript
         │
         ├── No MP4? ──► Player Pipeline
         │                  Playwright → headless Chromium
         │                  SCORM API shim injected
         │                  Smart slide sampling
         │                  Interaction detection (quiz/drag-drop/scenario/hotspot)
         │                  Drag-drop handler (4 strategies)
         │                  Claude Vision → analyze screenshots
         │                  Re-score with visual content
         │
         ▼
    Enriched JSON
         │
         ▼
┌─────────────────┐
│  generate_report│  WeasyPrint → consulting PDF
│                 │  Color-coded scores, before/after, recommendations
└─────────────────┘
```

---

## AI Readiness Score (0–100)

| Score | Tier | Meaning |
|-------|------|---------|
| 0–39  | 🔴 Not Ready | No metadata, no objectives, AI can't index this |
| 40–69 | 🟡 Partially Ready | Some content extractable, needs enrichment |
| 70–100| 🟢 AI Ready | Well-documented, skills mapped, objectives clear |

**What improves the score:**
- Course description present
- Learning objectives defined
- Keywords/skills tagged
- Duration metadata set
- Readable text content (transcripts, slide text)
- Assessment questions present

---

## Installation

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/scorm-ai-analyzer.git
cd scorm-ai-analyzer

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browser (for --player pipeline)
playwright install chromium

# Install ffmpeg (for --video pipeline)
# Ubuntu/Debian:
sudo apt install ffmpeg -y
# macOS:
brew install ffmpeg

# Set your Anthropic API key
export ANTHROPIC_API_KEY="your-key-here"
```

---

## Usage

### Single Course Analysis

```bash
# Basic parse (metadata only)
python3 scorm_analyzer.py course.zip

# With AI enrichment
python3 scorm_analyzer.py course.zip --llm

# With video transcription (for video-based courses)
python3 scorm_analyzer.py course.zip --video

# With browser player (for interactive Storyline/Rise courses)
python3 scorm_analyzer.py course.zip --player

# Save output to file
python3 scorm_analyzer.py course.zip --llm --output results/course.json
```

### Batch Analysis

```bash
# Analyze entire folder of SCORM ZIPs
python3 batch_analyzer.py scorm_inbox/ --llm

# Full pipeline with video transcription
python3 batch_analyzer.py scorm_inbox/ --llm --video

# Full pipeline with browser player
python3 batch_analyzer.py scorm_inbox/ --llm --player

# Output goes to: output/batch_YYYY-MM-DD/
# PDF report auto-generated at end
```

### Generate PDF Report

```bash
# From a folder of JSON results
python3 generate_report.py output/batch_2026-03-27/

# From a single JSON file
python3 generate_report.py results/course.json
```

---

## Files

| File | Purpose |
|------|---------|
| `scorm_analyzer.py` | Core analyzer — parser, video pipeline, player pipeline |
| `batch_analyzer.py` | Batch processing across a folder of ZIPs |
| `generate_report.py` | PDF report generator (WeasyPrint) |
| `generate_xlsx.py` | Excel export |
| `generate_playbook.py` | Remediation playbook generator |
| `scorm_player.py` | Standalone SCORM player (headless Chromium) |
| `requirements.txt` | Python dependencies |

---

## Pipelines In Detail

### Parser
- Reads `imsmanifest.xml` from the SCORM ZIP
- Extracts: title, description, keywords, version, language, duration, objectives, SCO count
- Detects SCORM version (1.2 vs 2004)
- Identifies authoring tool (Articulate Storyline, Rise, Lectora, etc.)
- Baseline LLM scoring via Claude

### Video Pipeline (`--video`)
1. Scans ZIP for MP4 files
2. Extracts audio with `ffmpeg` (mono, 16kHz WAV)
3. Transcribes with OpenAI Whisper (`base` model by default, configurable)
4. Feeds full transcript to Claude for enriched analysis
5. Returns before/after AI Readiness scores

### Player Pipeline (`--player`)
1. Extracts SCORM to temp directory
2. Spins up local HTTP server
3. Launches headless Chromium via Playwright
4. Injects SCORM API shim (so course doesn't freeze waiting for LMS)
5. Detects course type: Rise (scroll-based) vs Storyline (click-based)
6. **Smart sampling:** ≤15 slides → all; 16–50 → interval; 50+ → head/sample/tail
7. **Interaction detection** per slide: quiz, drag-drop, scenario, hotspot, video, standard
8. **Drag-drop handler** (4 strategies):
   - Strategy 1: JS drag event simulation
   - Strategy 2: Playwright mouse drag simulation
   - Strategy 3: Click all interactive elements
   - Strategy 4: Force SCORM API completion
9. Sends screenshots to Claude Vision for analysis
10. Returns enriched score with learning objectives, skills, audience

---

## Output Format

```json
{
  "source_file": "course.zip",
  "scorm_version": "1.2",
  "metadata": { "title": "...", "description": "...", ... },
  "organizations": [...],
  "resources": [...],
  "stats": { "ai_readiness_score": 28, ... },
  "llm_analysis": {
    "ai_readiness_score": 28,
    "ai_summary": "...",
    "inferred_skills": [...],
    "target_audience": "...",
    "learning_objectives": [...],
    "content_quality_flags": [...]
  },
  "video_pipeline": {
    "transcripts": { "video.mp4": "Full transcript text..." },
    "score_before": 28,
    "score_after": 62,
    "enriched_analysis": { ... }
  },
  "player_pipeline": {
    "course_type": "storyline",
    "sampling_strategy": "all",
    "total_slides_detected": 12,
    "screenshots_captured": 12,
    "stall_count": 1,
    "slides": [
      { "index": 1, "interaction_type": "standard", "stalled": false }
    ],
    "score_before": 28,
    "score_after": 55,
    "visual_analysis": { ... }
  }
}
```

---

## Requirements

- Python 3.8+
- Anthropic API key (Claude)
- ffmpeg (for video pipeline)
- Playwright + Chromium (for player pipeline)
- OpenAI Whisper (for video transcription)
- WeasyPrint (for PDF reports)

See `requirements.txt` for full list.
