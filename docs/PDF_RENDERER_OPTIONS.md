# PDF Renderer Options

**Documentation synchronized:** 2026-08-22

**Status: evaluation only.** This document does not change any code. It exists
to support a decision on whether to replace the hand-rolled PDF writer in
`app/reporting/final_pdf.py` with a third-party PDF library. No implementation
should proceed from this document without explicit review and sign-off — see
`AGENTS.md`'s non-negotiables on not inventing production facts or silently
expanding scope.

## 1. What exists today

`app/reporting/final_pdf.py` is a ~200-line, dependency-free PDF writer. It
builds PDF objects by hand: a `/Catalog`, a `/Pages` tree, one `/Font` object
(standard Helvetica, no embedding), one `/Page` object per page, and one
content stream per page built from plain text lines via manual `BT`/`Tj`/`T*`
operators. Pagination is a fixed line count (`LINES_PER_PAGE = 56`); word
wrap is a naive character-count splitter (`_wrap`, `WRAP_WIDTH = 104`) that
breaks on the nearest space, or mid-word if none is found in range. Content
streams are **not compressed** — this matters later (§4).

This buys real things: zero dependencies, a fully auditable ~200 lines
covering the entire PDF spec surface this project needs, and no Docker image
or dependency-compatibility risk. It also has real limits:

- **No tables.** Every finding is five separate text lines (`result_id: ... |
  module: ... | site: ... | check_id: ...`, `target: ... | status: ...`,
  `message: ...`, `expected: ...`, `actual: ...`) rather than one scannable
  row. Section summaries (`_summary_lines`) are the same: one flat line per
  site/module, not a table.
- **No clickable evidence links.** `_evidence_lines` writes
  `path: runs/<run_id>/<module>/result-N.json` as plain text. A reviewer has
  to copy that path and know how to construct the `/api/evidence/{path}`
  URL themselves.
- **No visual distinction for headers.** `_page_stream` uses one font
  (Helvetica, 9pt) for every line — including what are conceptually section
  headers (`"Site Summaries"`, `"Automated Findings"`, `"Reviewer Notes"`).
  There's no bold, no size change, no color, so a header is only
  distinguishable from data by reading its content.
- **No status color-coding.** `PASS`/`FAIL`/`WARNING`/`ERROR` all render
  identically. For a document whose entire purpose is fast human review,
  this is a real usability gap — the HTML review UI already does this via
  `status-badge` CSS classes (`app/web/static/app.css`); the PDF doesn't.
- **No document metadata.** No `/Info` dictionary — no `Title`, `Author`,
  `Subject`, or `CreationDate`. Every generated PDF shows up in a viewer's
  title bar as just its filename.
- **Latin-1-only text.** `_page_stream` encodes with
  `.encode("latin-1", "replace")` — any reviewer note, dashboard name, or
  target containing a non-Latin-1 character (e.g. an accented name) silently
  becomes `?` in the final archived record.

## 2. What reviewers would plausibly want

Given the review workflow this PDF is the permanent record of (`docs/ARCHITECTURE.md`
§8, `docs/VALIDATION_CATALOG.md` §14), the highest-value additions are:

1. **A results table** (Module | Check | Status | Message) instead of five
   lines per finding — the single biggest readability win for a document
   that can run to dozens of results.
2. **Clickable evidence links** pointing at `/api/evidence/{path}` (routed
   through `app/api/routes_evidence.py`). Worth being honest about the
   limit here: these are internal, auth-protected URLs — clicking one only
   works if the reviewer opens it in a browser where they're already
   authenticated to the running application, not as a universally portable
   link. Still strictly better than a path the reviewer has to
   copy/paste/construct manually.
3. **Status color-coding** (green PASS, red FAIL/ERROR, amber WARNING)
   matching the existing HTML review UI's palette, for fast visual scanning.
4. **Styled section headers** (bold, larger, maybe a rule line) so the
   document has actual visual hierarchy instead of uniform 9pt text
   throughout.
5. **Document metadata** — `Title` = run ID, `Author` = reviewer,
   `CreationDate` = snapshot timestamp. Low effort, meaningful for anyone
   browsing an archive of these PDFs later.

## 3. Candidates considered

| Library | Type | Native/system deps | Approx. installed size | Python 3.14 wheel risk | License |
|---|---|---|---|---|---|
| *(current)* hand-rolled | stdlib only | none | 0 — already in the repo | none | project code |
| **reportlab** | mature layout engine (Platypus: flowables, auto-sizing tables, paragraph reflow) | optional C accelerator (`_rl_accel`), pure-Python fallback exists | ~10–15 MB | **unverified for cp314 as of writing — must confirm before pinning** | BSD (open source) |
| **fpdf2** | lightweight, direct drawing API + `table()`/`multi_cell()` helpers | none — pure Python | ~2–3 MB | none — ships as a universal wheel regardless of platform/Python version | MIT |
| WeasyPrint | HTML/CSS → PDF renderer | requires system Pango, Cairo, GDK-Pixbuf via `apt`, not installable via `pip` alone | large — adds a system-package layer, not just a Python dependency | Docker-image complexity independent of Python version | BSD |

WeasyPrint is ruled out early: it would require adding `apt-get install`
steps to the `Dockerfile` (currently `python:3.14-slim-bookworm` + `pip
install` only, per `docs/ARCHITECTURE.md` §2 and `Dockerfile` itself),
turning a single-stage, pip-only build into one with system-library version
management. That's a materially different build/maintenance story than "add
a line to `requirements.txt`," for a feature that doesn't need HTML/CSS
rendering in the first place — this project's PDF content is structured
data, not a styled webpage.

reportlab vs. fpdf2 is the real decision, and it isn't obvious — see §5.

## 4. Migration risk: the two assertions that treat PDF bytes as text

This is the part of the task most worth getting right, because it's not
just a unit test — one of the two affected checks is a release-blocking gate.

**`tests/unit/test_reporting.py::test_multi_page_pdf_contains_snapshot_sections`**
currently does:
```python
self.assertGreater(data.count(b"/Type /Page"), 1)
self.assertIn(b"Reviewer Notes", data)
self.assertIn(b"build_id: test-build", data)
self.assertIn(b"configuration_hash: hash", data)
self.assertIn(b"module note appears", data)
self.assertIn(b"splunk note appears", data)
self.assertIn(b"Evidence References", data)
```

**`scripts/ci_e2e.py`** — part of the `Safe E2E` gate, one of the
release-blocking checks listed in `docs/CI_CD.md` §5/§6 and
`docs/VALIDATION_CATALOG.md` §16 (`python scripts/ci.py e2e`) — does the
same thing for every persisted reviewer note:
```python
pdf_bytes = pdf_path.read_bytes()
for text in note_texts:
    assert text.encode("latin-1") in pdf_bytes, f"missing note from final PDF: {text}"
```

Both work today **only because the current renderer never compresses
content streams** — the literal text bytes sit uncompressed in the file, so
a raw substring search finds them. This is not a safe assumption to carry
forward:

- **reportlab and fpdf2 both compress content streams by default**
  (FlateDecode/zlib), so `assertIn(b"module note appears", data)` would
  almost certainly fail against real output from either library — not
  because the text is missing, but because it's deflate-compressed and no
  longer a literal byte sequence in the file.
- **Even the "structural" assertion is more fragile than it looks.**
  `data.count(b"/Type /Page")` depends on the exact byte sequence
  `/Type /Page` — the space matters. A different library could just as
  validly emit `/Type/Page` (no space) and still be a perfectly correct
  PDF; the test would then report zero pages found, not because the PDF
  is broken but because the assertion is coupled to one specific writer's
  formatting style, not to the PDF spec itself.

**What actually needs to change:** both assertion blocks need to move from
"grep the raw file for text" to "parse the PDF and extract its text," using
a PDF-reading library — `pypdf` is a reasonable, pure-Python (no native-wheel
risk, same reasoning as fpdf2 in §5) choice for this, since it's read-only
work (page count, `extract_text()`) rather than the richer generation
capability a writer library provides. This is real, non-trivial migration
work, not a side effect that fixes itself:

1. Add a small shared test helper (e.g. `_extract_pdf_text(pdf_bytes) ->
   str`) used by both `test_reporting.py` and `ci_e2e.py`, so the two don't
   duplicate PDF-parsing logic.
2. Rewrite the byte-`in`-file checks as substring checks against extracted
   text per page (or joined across pages).
3. Rewrite the page-count check to use the reading library's own page count
   (`len(PdfReader(...).pages)`) instead of counting a byte pattern.

**A mitigating factor:** the *call site* churn is small. `render_final_pdf(snapshot,
output_path) -> (path, checksum)` in `app/reporting/final_pdf.py` and
`render_pdf_under(root, snapshot, relative_path)` are the only entry
points `app/reporting/snapshot.py`'s `finalize_run()` depends on — if the
new implementation keeps that exact signature, `snapshot.py` needs zero
changes. The cost is concentrated entirely in the two test files above, not
spread across the codebase. `app/reporting/html.py` (the separate HTML
review renderer) and `app/api/routes_reports.py` (which just serves the PDF
file via `FileResponse` and doesn't care how it was built) are both
unaffected either way.

**A way to de-risk the rollout:** both reportlab and fpdf2 support disabling
compression (reportlab: `Canvas(..., pageCompression=0)`; fpdf2 exposes
`set_compression(False)`). A two-phase migration is a reasonable way to
avoid doing "swap the renderer" and "rewrite the CI-gating test correctly"
as one all-or-nothing change:

- **Phase 1:** swap the renderer, keep compression *off* transitionally, and
  adjust only the minimal structural assertion that changes (the exact
  `/Type /Page` byte pattern, if the new library formats it differently).
  Text-content assertions keep working almost unchanged since streams stay
  uncompressed.
- **Phase 2 (separate, deliberate step):** rewrite `test_reporting.py` and
  `ci_e2e.py` to use real PDF text extraction, then turn compression back on
  to actually get the file-size benefit.

## 5. Recommendation

**Recommend fpdf2 over reportlab**, with reportlab as the documented
runner-up if the maintainer is comfortable taking on the risk in the next
paragraph.

The deciding factor is specific to this project's own history, not a
general library-quality judgment: `docs/PROJECT_BUILD_REPORT.md` §2
documents that the Python 3.14 migration *already* required chasing down
"Python-3.14-compatible PyYAML" and "Python-3.14-compatible Psycopg binary"
pins — this project has concretely paid the cost of a dependency not yet
having a compatible wheel for a very recent Python version, twice. reportlab
ships an optional C-accelerated extension (`_rl_accel`); whether a prebuilt
wheel exists for `cp314` at whatever version gets pinned is unverified as of
this writing and would need to be checked (`pip install reportlab` against
the actual `python:3.14-slim-bookworm` build target) before committing to
it. fpdf2 is pure Python with no C extension at all, so it structurally
cannot hit that failure mode — it always installs as a universal wheel
regardless of Python version or platform. Given this project has hit exactly
this class of problem before, weighting it heavily here is a
project-specific judgment call, not a generic "smaller is always better"
one.

The trade-off being accepted: reportlab's Platypus layout engine (automatic
table column sizing, paragraph reflow across page breaks, a decade-plus of
production use) is more capable and more battle-tested than fpdf2's more
direct, lower-level API. fpdf2 can still do everything §2 asks for —
`table()` for structured findings with header-row styling, `cell(...,
link=...)` for clickable evidence links, colored text via `set_text_color()`,
embedded TrueType fonts for full Unicode (also fixing the current
Latin-1-only limitation) — it just takes a bit more manual layout work per
feature than reportlab's higher-level flowables would. For this project's
scope (one fairly fixed report structure, not arbitrary rich documents),
that's an acceptable trade for removing a real, previously-experienced
category of build risk.

If reportlab is verified to have a working `cp314` wheel and the maintainer
would rather have the more capable layout engine, that's a legitimate
alternative choice — the two are close enough on merits that this shouldn't
be read as reportlab being a bad option, just the one carrying the
higher-risk dependency for this specific project.

## 6. Explicitly out of scope for this document

- No code changes accompany this document.
- No dependency has been added to `requirements.txt`.
- No test has been rewritten.
- Whichever library is chosen, verifying it actually has a compatible wheel
  for the pinned Python 3.14 patch version used in `Dockerfile` is a
  required pre-implementation step, not an assumption to carry into a
  future task.

Implementation should be a follow-on task, scoped to include the test
rewrite in §4 as a required part of the same change — not a fast-follow,
since shipping the renderer swap alone would silently break a
release-blocking CI gate (`scripts/ci.py e2e`).