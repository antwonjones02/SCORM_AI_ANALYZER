# Authoring-tool format reference

Verified internals of major e-learning authoring tool exports, used by
`scormlib/content.py` and `scormlib/package.py` for deterministic extraction.
Findings verified against real exported packages and open-source parsers.

## Encoding cheat-sheet

| Tool | Content carrier | Encoding |
|------|-----------------|----------|
| Articulate Storyline | `html5/data/js/*.js` | `window.globalProvideData('<kind>', '<JS-escaped JSON>')` — decode JS escapes by hand (NOT `unicode_escape`, it mangles UTF-8) |
| Articulate Rise | `scormcontent/index.html` or `scormcontent/locales/und.js` | base64 JSON via `window.courseData = "…"`, `__resolveJsonp("course:und","…")`, or `deserialize("…")` |
| Adobe Captivate | `project.txt` (root) | plain JSON despite the .txt extension |
| Adobe Captivate | `assets/js/CPM.js` | minified JS object literal — regex `accstr:'…'` only |
| iSpring | `index.html` `var presInfo = "…"` | base64 **+ zlib** JSON |
| iSpring | `data/quizN.js` `var quizInfo = "…"` | base64 JSON (no zlib!) |
| Lectora | per-page HTML (`pageN.html` / `aNNN*.html`) | text in `<span class="textNNFontM">` runs, sometimes inside escaped JS |
| Camtasia | `<name>_config.xml` | XMP/RDF XML |
| AICC | `.crs` (INI) + `.des`/`.au`/`.cst` (CSV) | plain text |

## Storyline (`globalProvideData` kinds)

- `data` (`html5/data/js/data.js`): `scenes[].slides[].title`, `slideCount`,
  `quizzes[].lmstext` (quiz title) + `passPercent`, `assetLib[].captions`
  (path to caption .js). Skip scenes with `isMessageScene: true`.
- `slide` (`html5/data/js/<id>.js`): per-slide `title` (often "Untitled
  Slide" — filter it), `slideLayers[].timeline.duration` (**ms** — sum for
  course duration), text under `textLib`/`vartext.blocks[].spans[].text`,
  quiz `interactions[]`: `lmstext` = question, `choices[].lmstext` = options,
  `answers[]` with `status:"correct"` → `evaluate.statements[].choiceid`.
- `frame` (`frame.js`): `navData.outline.links[].displaytext` = authored
  slide/lesson titles (best source); `controlOptions.sidebarOptions.titleText`
  = course title.
- `caption` (`story_content/*_captions.js`): `{"data": "<percent-encoded
  WEBVTT>"}` — `urllib.parse.unquote` then parse as VTT.
- Root `meta.xml`: `<project title="…" duration="About 12 minutes">`,
  `<slidemeta viewslides="23">`, `<application name="Articulate Storyline">`.

## Rise

Decoded JSON: `course.title`, `course.description` (HTML),
`course.lessons[]` with `type == "section"` = group headers (not content).
Lesson `items[]` blocks → sub-items carry `heading`/`paragraph`/`title`/
`caption`; knowledge checks have `answers[]: {title, correct}`.
No authoritative duration exists in Rise exports.
Captions: real `.vtt` files under `scormcontent/assets/`.

## Captivate

`project.txt` → `metadata.{generator, title, durationInFrames, frameRate}`
(**duration sec = frames / rate**; title often placeholder — verify),
`toc[].title`, `contentStructure[]` nodes with `class == "Question Slide"` →
`roles.question.text` (question availability varies by version; answer text is
NOT here — only ids). Visible/answer text: `CPM.js` `accstr:'…'` fields
(filter auto-names like "Rectangle 12"); much body text is baked into
`dr/*.png` images (unrecoverable without OCR).

## iSpring

`presInfo` JSON: `t` = title, `ui` = generator string (`issuite_…`), `s[]`
slides with `t` (title) and `x` (**flat plain-text of the slide**, `\r\n`
separated — best text source), `e[].p` event timestamps (≈ slide duration).
`quizInfo` JSON: `d.T` quiz title, `d.sl.g[].S[]` questions with `D.a`
(plain) / `D.h` (HTML) prompts; choice text in `C.rt.h` (HTML).

## Camtasia

`<name>_config.xml` (contains `techsmith` namespaces): `dc:title`,
`<xmpDM:duration xmpDM:scale="1/1000" xmpDM:value="54200"/>` → ms.
Content is video — text only via captions.

## Detection fingerprints (order matters)

| Signal | Tool |
|--------|------|
| `story_content/` dir or `meta.xml` `<application name="Articulate Storyline">` | Storyline |
| `scormcontent/index.html` (+ manifest org id `articulate_rise`) | Rise |
| root `project.txt` with `"generator":"Captivate"` or `assets/js/CPM.js` | Captivate |
| `data/player.js` + `presInfo` in index | iSpring |
| `trivantis-*.js` files | Lectora |
| `techsmith-smart-player.min.js` | Camtasia |
| `adapt/` + `course/<lang>/*.json` | Adapt framework (also Evolve) |
| `tincan.xml` / `cmi5.xml` / `*.crs` at root | xAPI / cmi5 / AICC |

Caution: `scormdriver/` alone is just the Rustici SCORM Driver — used by Rise,
dominKnow and others. Not tool-specific.

## AICC field map

`.crs` INI: `[Course] Course_Title`, `[Course_Description]` body.
`.au` CSV: `file_name` = launch, `mastery_score`, `Max_Time_Allowed` (HH:MM:SS).
`.des` CSV: per-AU `title`,`description` joined on `system_id`.
`.cst` CSV: `block,member` hierarchy rows.

## cmi5.xml essentials

`<course id>` + `<title><langstring lang>` + `<description>`; `<au>` with
`moveOn`, `masteryScore` (0-1), `<url>` = launch. No duration element.
