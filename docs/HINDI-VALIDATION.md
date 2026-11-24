# Hindi extraction and page-view validation

## Root cause and fix
`coi-hindi.pdf` uses XpressChanak glyph encoding rather than Unicode Hindi. The earlier implementation used OCR on every text region. The new path decodes supported glyphs directly, reorders matras/reph, preserves raw text and geometry, and falls back to OCR for unsupported symbols. No extraction or translation is automatically approved.

The simple reader now presents editable text on one white page instead of paragraph cards. PDF coordinates, baseline spacing, font size and available bold/italic information control placement. Both panes share page zoom. Saved region IDs, revision checks, drafts and approvals remain intact. Long replacement text that exceeds its original space is marked and can be scrolled while editing. Exact font reproduction, illustrations, and complex mixed typography are not recreated.

## Dataset and accuracy checks
- Local supplied PDF: 544 pages; SHA-256 `020206e0904a285dca191065be61f88d5e9bfdd8df2d4c1f693be821cad27192`.
- All 4,769 legacy text blocks audited: 4,758 decode; 11 ambiguous blocks require fallback. This measures mapping coverage, **not** human-verified accuracy of every page.
- Cover and article 16 paragraphs manually transcribed against rendered pages; exact-string golden fixtures included in tests.
- Additional cases: article 124, 1956/1995/2001/2015 references, नियुक्ति, मूलवंश, धर्म, वर्गों, ईंधन, पिछड़े, mixed English/Hindi spans, unsupported fonts/glyphs, native Unicode, unchanged originals, stale revisions, and preservation of saved corrections.
- Browser checks: exact cover, article numbers/dates, Hindi → English suggestion, review gate, Unicode text download, single page surface, borderless editing, original margins/side-note positions, fit/150% zoom, no clipped source text on selected pages, mobile width and browser errors.

## Performance measured on this machine
The same in-process extraction function was measured with decoding disabled (OCR baseline) and enabled. These figures exclude worker startup, API calls and UI polling; timings vary with system load.

| PDF page | OCR baseline | Direct decoding |
| --- | ---: | ---: |
| 1 | 0.693 s | 0.010 s |
| 41 | 5.351 s | 0.071 s |
| 101 | 6.357 s | 0.104 s |

Full-document mapping audit took 3.24 s. Initial browser job-to-visible-text measurements were about 1.9 s for pages 41 and 101; later loaded-machine runs were 7.8–11.7 s. Full-document application extraction still includes a bounded worker per page and table/form inspection, so the mapping audit is not a promise of a three-second full import.

## Repeatable commands
Use a disposable data directory for tests, never the user's live database. From `backend`:

```powershell
$env:DATA_DIR = "$env:TEMP/pdf-reader-hindi-tests"
$env:UPLOAD_DIR = "$env:TEMP/pdf-reader-hindi-tests/uploads"
$env:HINDI_ACCEPTANCE_PDF = 'C:\Users\Shresth Singh\Downloads\coi-hindi.pdf'
$env:RUN_LOCAL_MODEL_TESTS = '1'
$env:RUN_LOCAL_OCR_TESTS = '1'
& .venv-local/Scripts/python.exe -m pytest tests -q
```

The PDF is not committed. Exact golden fixtures run without it; the 544-page acceptance test requires the environment variable. Browser tests require a separate QA server on port 8002 and the frontend production build:

```powershell
# From frontend, with the same HINDI_ACCEPTANCE_PDF:
node node_modules/typescript/bin/tsc --noEmit
node node_modules/vite/bin/vite.js build
node tests/hindi-browser.mjs
node tests/reader-browser.mjs
```

## Mapping provenance
The mapping table was adapted from [typesetu](https://github.com/ozairexe/typesetu/blob/dedc9e7d068e1d90bbe43db0b043cd4b1d5ced07/assets/engines.js), under its [MIT license](CHANAKYA-MAPPING-LICENSE.txt). Xpress-specific byte recovery, strict validation and positional handling are implemented separately. No third-party font files were imported.

## Final results — 22 November 2026
- Backend: **470 passed, 1 skipped**, with real local OCR/model tests and the supplied-PDF acceptance test enabled. The skip is the optional live-server test; six dependency deprecation warnings remain.
- Focused Hindi regression suite: **33 passed** after adding page-layout metadata.
- TypeScript validation and production build passed.
- Hindi browser acceptance passed, including one page surface, no borders, unclipped source text, margins/side notes, shared zoom, download and mobile checks. Final measured jobs reached visible text in 2.65 s (page 41) and 2.59 s (page 101).
- All **20** existing reader workflow checks passed: saved edits, recovery, real offline translation, export formats, detailed-review handoff, missing-model handling, scan recognition, and mobile layout.
- User's existing Hindi pages 1 and 41 were safely refreshed; original bytes and saved corrections remain intact. Page 41 was visually inspected side by side in the running app.

## Alignment refinement — 24 November 2026
- Added individual line positions and widths so paragraph indents, short final lines, side notes and footnotes track the original more closely.
- The reading preview adjusts word spaces only; Hindi letters and combining marks are not stretched apart. Clicking a passage keeps the existing editable text and save workflow.
- Geometry is bound to the original extracted text; corrected text and translations use the regular editor instead of stale line positions.
- Joined adjacent same-font fragments before decoding pre-base matras; visible gaps between those fragments preserve word boundaries. Original extraction evidence is unchanged.
- Added regression cases for split कि, separate भारत का, source line indents and baselines, and edited-text fallback. Hindi browser acceptance and all 20 reader workflow checks passed; TypeScript and production build passed.
- Final backend regression: **473 passed, 1 skipped**, with local OCR/model and supplied-PDF acceptance checks enabled.
