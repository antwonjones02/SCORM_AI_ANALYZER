# Test Fixtures

SCORM/xAPI packages used by automated tests for the analyzer and the headless-browser
SCORM player. Source trees live in `src/<package_name>/`; run
`python3 tests/fixtures/build_fixtures.py` (idempotent) to produce `<package_name>.zip`
with the manifest at the archive root.

## Fixtures

- **course_basic_12.zip** — Complete SCORM 1.2 package ("Workplace Fire Safety Essentials")
  with full inline LOM metadata, 3 organization items, `adlcp:masteryscore`, and a 6-screen
  vanilla-JS SCO (welcome, 2 content slides, 2 radio-button quiz questions, completion) that
  reports lesson_status, interactions, score, and session_time via the SCORM 1.2 API, with a
  safe no-op fallback when no LMS is present.

- **course_minimal_2004.zip** — Minimal SCORM 2004 3rd Edition package ("Untitled Module 7")
  for sparse-metadata scoring: no description, keywords, or duration; a single static page
  that calls `API_1484_11` Initialize / SetValue(completion_status=completed) / Commit / Terminate.

- **course_rise_like.zip** — Mimics an Articulate Rise export ("Data Privacy Basics"): SCORM 1.2
  manifest launching `scormdriver/indexAPI.html`, which redirects to a long (4000px+) scrollable
  `scormcontent/index.html` with 6 lessons; sets lesson_status completed on scroll-to-bottom.

- **course_tincan.zip** — xAPI/TinCan package ("Cyber Hygiene Fundamentals") with NO
  imsmanifest.xml: Articulate-style `tincan.xml` declaring activity id
  `http://example.com/xapi/course/cyber-hygiene` and launch `index.html` (one static page).
