# Local development and testing

The local app now starts in the simple reader described in [README.md](../README.md). Open a PDF to extract its first page automatically, edit or translate its text, and download a review copy. Use **Detailed review** for the specialist workflows below. [The November usability validation](USABILITY-VALIDATION.md) records current checks. `Start-Local.ps1` now rebuilds the frontend through Node when available; use `-SkipBuild` to reuse an existing build.

This is a development implementation, not a qualified universal PDF translator. Original bytes, edits, review records and exports remain local. `backend/local-data` and `backend/models` are ignored by Git. Do not publish those directories.

## Start on this computer

Run `./Start-Local.ps1` from the project root, then open http://127.0.0.1:8000. The script uses `backend/.venv-local`, serves the built frontend and binds only to loopback. Existing `.venv` and `.venv-repaired` point at a missing interpreter; do not use them. Python 3.12.14 was installed into the new isolated environment during development.

For a new computer: install Python 3.12 and Node, then run `./Setup-Local.ps1 -PythonExecutable <python-executable> -InstallOcrData`. Install the Tesseract executable separately. Windows Nirmala fonts are used for Hindi PDF output; the app does not redistribute Windows font files. A local NotoSansDevanagari-Regular.ttf may instead be placed in backend/models/fonts.

Add `-InstallTranslationModels` to install the hash-pinned optional CPU runtime and the two public native OPUS-MT models. Both directions are already installed on this computer. Setup downloads model archives explicitly; normal inference works offline. Exact existing installations are hash-verified and reused. Changed or damaged installations are left untouched with a repair instruction. The core lock and optional translation requirements are separate compatible layers; see DEPENDENCIES-MODELS.md for versions, licenses and provenance.

For frontend development, run `npm run dev` from frontend with the backend on port 8000. The local production build always uses its own server origin, irrespective of old hosted Vite API settings. `VITE_RUNTIME_MODE=hosted` explicitly selects the older hosted UI; it does not enable the local review API remotely.

After a code update, save your work and stop the existing local server before starting it again with `./Start-Local.ps1`. Refresh the browser afterward. A running Python process keeps its loaded code even when files on disk change. The manual-table editor checks the server's reported capability; if it is missing, the page shows a restart instruction instead of offering an unsupported save action. This does not delete or re-import documents.

## Review a document

1. Import. If inspection is paused for memory/password/format, the original remains downloadable. Resolve the indicated condition and retry inspection.
2. Extract a page or the remaining document. Jobs checkpoint pages, pause and resume. Re-extraction does not overwrite existing reviewed pages.
3. Compare the full page with detected boxes. Draw missing regions. Use Move earlier / Move later to correct headings, columns and note order. Ordering changes preserve text/evidence and clear dependent translation/completeness approvals. OCR alternatives are evidence; adopt/correct a transcription explicitly.
4. For detected native ruled tables, compare the proposed cells, blank cells and merged spans with the original before adopting them. Use Build or correct a table grid for an isolated missed table or an unadopted detector proposal, following the workflow below. Review structural relationships separately from each cell's text. For form fields, compare the saved field value with its visible appearance and record how discrepancies were resolved. Use Refresh form evidence for missing or incomplete legacy evidence. Fields that still cannot be inspected remain unresolved; XFA needs a separate static printable source.
5. Confirm source language and disposition. Review the source, then select Generate page translations to use the installed offline draft model, or enter a translation manually. Review every displayed meaning check with a specific note. A model output is never approved automatically.
6. Record whole-page completeness. Create a review copy. Review every exported PDF page. The final-download gate additionally checks that the source revision remains current.
7. Source/translation/context/glossary changes invalidate approvals. TXT/DOCX remain review copies because their final presentation has not been independently qualified.

Preserved images containing untranslated text, unreadable content and exclusions keep output partial. Hindi exports now keep their visible letters as vector outlines and add a separately checked searchable text layer. The difficult two-page Hindi fixture passes both MuPDF and PDF.js 6.3.289 extraction. Appearance or logical-text differences block export. Text selection geometry is approximate within a shaped line; the uploaded original is untouched. This is not a claim of compatibility with every PDF reader. Faithful export currently handles supported digital text boxes; image backgrounds, forms and annotations fail explicitly into readable/companion/manual review workflows. Companion output rasterizes the original's visible pages and appends translation; it is not an exact-layout translated replica and carries no original digital-signature certification.

Layout-preserving export also checks the whole page's copied-text order. If retained original text appears before an earlier translated paragraph in the PDF's logical order, choose readable or companion mode. Original Devanagari that remains selectable after replacement is not covered by the authored-text encoding checks and blocks layout-preserving export. Fully replaced Hindi-to-English text can still pass. Export verification preserves word boundaries and rejects added text, changed amounts and duplicates within each text allocation. The current PDF qualification version is 5, which requires form-inventory/mapping checks and strict table-grid/member validation. Older exports, including version 4 exports with saved layout approvals, remain review copies; regenerate them and review their pages before final download. Readable PDF and DOCX preserve adopted table cells/spans, including bounded visual source crops; TXT labels their row/column/span positions.

## Define a missed or ambiguous table

1. Save or discard pending edits. On an extracted page, open Build or correct a table grid. Select New table or the unadopted detector proposal to replace.
2. Draw around the entire table on the original preview, or enter its outer bounds as percentages of the page. Define row/column counts and use Space boundaries evenly. For unequal cells, edit the comma-separated boundary percentages, including 0 and 100.
3. Add merged cells by their starting row/column and span. Row/column numbers in the editor start at 1. Merges cannot overlap or leave the grid. Limits are 40 rows, 20 columns and 200 grid positions per table.
4. Explicitly select every untouched source region covered by the table. A region that crosses the boundary cannot be replaced. Saved edits, approved regions, form fields, adopted table cells and conflicting proposals are protected. Adjust the boundary or leave the table unresolved when it cannot be isolated safely.
5. Enter the grid and replacement review note, then create the unreviewed grid. A changed page revision blocks the stale draft; start again against the current page. A failed save retains the draft and selections. Unfinished grids are saved in this browser for this document/page and recovered on reload. Create the grid or explicitly Discard grid draft before changing documents/pages or importing another file. A storage failure shows a warning: keep the page open until you resolve or discard the draft. Drafts are browser-local and are not part of workspace backups.
6. Transcribe each cell against the original. The Source reference archive retains the replaced recognition text, source geometry and OCR alternatives. New cells are unreadable until reviewed; a visually empty cell must be explicitly confirmed as decorative/blank. Text is never distributed among cells automatically.
7. Review every cell's text and language, then separately approve the headers, values, blank cells and spans. Recheck whole-page completeness and dependent translations. Creation clears dependent approvals; it does not certify the grid or content.

This supports isolated regions from native or scanned recognition. A full-page scan crossing a smaller table's boundary needs a future region-splitting workflow. Existing edited or adopted grids cannot be replaced by this command. Cross-page tables, automatic recognition of arbitrary tables and general grid correction remain incomplete. Original PDF bytes stay unchanged; archived recognition is evidence and is not duplicated in exported content.

## Refresh missing or incomplete form evidence

This workflow is available on an extracted page. It re-inspects the original PDF's static fields without running OCR or replacing page transcription.

1. Save or discard unsaved region edits. Open Refresh form evidence and select Inspect form evidence. The bounded worker verifies the original fingerprint and reads its field inventory. Inspection alone does not change saved content or approvals.
2. Compare each field's stored value, appearance evidence and original position with the page. Select its matching saved form region. The preview suggests only an unambiguous match; overlapping legacy fields need an explicit choice. Every original field and every saved form region must be mapped exactly once. Use Add a separate field region only for a newly identified field, not as a replacement for an existing region.
3. Check all preview pages and enter an Evidence mapping review note of at least 10 characters. Select Apply evidence mappings. The server rechecks the original fingerprint, preview identity, document revision and page revision. If another edit or inspection made the preview stale, inspect again; the failed apply leaves saved content intact.
4. Reconcile any changed fields against the original and approve their source again. Saved source text, translations, raw extraction, notes, region identities, positions and reading order are retained. New fields start unresolved. Changed evidence clears the affected source approvals and conservatively clears translation, table-structure and completeness approvals across the document. An identical refresh leaves content revisions and approvals unchanged.
5. Complete the required reviews and create a new export when the document revision changed. Applying mappings does not approve the field contents or restore the validity of an original digital signature.

The workflow cannot repair an incomplete field inventory, unsupported XFA, unreadable evidence or an original whose bytes changed. It refuses conflicting known field identities or positions rather than moving saved edits to another field. Ambiguous legacy associations remain a human mapping decision; agreement of stored and appearance data does not establish visual correctness. If a complete static inventory cannot be obtained, retain the unresolved state and obtain a separate static printable source. Refresh does not enable form replacement in layout-preserving export.

## When inspection pauses for RAM

The default worker requires at least 1,024 MB of available system RAM before starting. This is available computer memory, not disk space or the uploaded file's size. The app displays the measured available/required amounts and provides Check available RAM. Close unused applications or browser tabs, check again, then select Retry inspection / unlock. Use Resume for a paused processing job. Do not upload another copy. Page count pending inspection means parsing has not yet succeeded; the original is saved and downloadable. A password is needed only for an encrypted PDF.

The threshold can be crossed as other applications allocate memory. On this computer, an observed 1,013 MB blocked inspection; after 1,239 MB became available, the same guarded retry successfully counted 200 pages in the user's latest Indian Easements Act upload. This establishes inspection recovery, not OCR or translation quality for the book. Keep production safety limits enabled. Tests use separate directories and sometimes a lower reserve for tiny synthetic fixtures; that does not qualify production workloads.

## Offline draft translation and optional qualification

The installed baseline uses native public OPUS-MT/Tatoeba checkpoints converted to CTranslate2 INT8, with separate Hindi-to-English and English-to-Hindi models. It verifies file hashes/runtime versions, rejects unsupported tokens and unfinished generation, deliberately splits long input and records source offsets. It does not understand a whole book or enforce context/glossary instructions as a chat model would. Terminology and legal scope remain explicit review checks.

Real three-page synthetic API pilots passed in both directions, including queued inference, persisted drafts, PDF generation and refusal of unapproved final download. A Hindi debtor/100-rupees example produced an incorrect actor/currency rendering, which the number/fact checks flag. These models are not certified for legal translation. Reports are in docs/measurements; human bilingual assessment and the planned 20-page feasibility pilot remain outstanding.

IndicTrans2 remains a separate optional upgrade:

Expected model IDs are ai4bharat/indictrans2-en-indic-dist-200M and ai4bharat/indictrans2-indic-en-dist-200M. Put complete approved local snapshots in backend/models/en-hi and backend/models/hi-en. The upstream download is gated: obtain authorized access and accept applicable licensing through the provider. No credentials were supplied during this implementation.

Install and pin a compatible CPU torch/transformers/IndicTransToolkit stack in an isolated qualification environment. Follow the upstream reference inference API: https://github.com/AI4Bharat/IndicTrans2/tree/main/huggingface_interface. Run the 20-page feasibility pilot, measure memory/time and have bilingual reviewers evaluate full clauses. Record exact versions and report qualified=false on failure. Only after a passing reviewed pilot run `python -m tools.qualify_model <model-directory> --direction en-hi --revision <commit> --pilot-report <report.json> --reviewer <name> --confirm-license-and-code-review` (repeat hi-en).

The adapter checks hashes, refuses unrecorded files, uses local_files_only and offline environment flags, bounds preprocessed tokens, and rejects generation that reaches its output limit. It never claims that a dictionary replacement is contextual model inference. Larger models, Docling and PaddleOCR are not qualified/enabled in this build.

## Optional approved cloud

Disabled by default. Configure GEMINI_API_KEY, GOOGLE_READONLY_ACCESS_TOKEN, GOOGLE_PROJECT_NUMBER and CLOUD_POLICY_REVIEWED_ON in the backend environment only. The OAuth token needs read access for API key lookup and billing information. The app checks that the key belongs to that numeric project and that billingEnabled is explicitly false with an empty billingAccountName. It never enables billing.

The policy review date expires after seven days. Recheck https://ai.google.dev/gemini-api/docs/pricing and https://ai.google.dev/gemini-api/terms before renewing it. Only Gemini 2.5 Flash standard text generation is implemented. Each request displays the complete fixed payload (including glossary/context), requires an explicit public/nonpersonal/nonconfidential declaration and is usable once. Changed or stale content requires a new preview. Quota errors pause; there is no paid fallback or search/file/tool access. External administrators can change billing state after a check; keep the dedicated project unbilled and restrict administrator access. No live cloud inference was performed in development.

## Tests

From backend: `.venv-local/Scripts/python.exe -m pytest tests -q`. Legacy live-provider tests are opt-in and are not evidence for the local workflow. From frontend: `npm run build`; browser test: `npm run test:browser` against a running server. The test includes a synthetic fixture at `frontend/tests/fixtures/sample.pdf`. Set REVIEW_URL and REVIEW_FIXTURE to override the server or fixture for an isolated QA workspace. The browser suite imports synthetic data and edits it, so use a separate QA data directory.

Use `node tests/structure-browser.mjs` for table/form workflows and `node tests/translation-browser.mjs` for actual model inference. They require explicit REVIEW_URL on a separate loopback port and refuse port 8000. The ordinary review suite expects missing models; configure its QA MODELS_DIR to a separate empty directory. The real-model suite needs MODELS_DIR pointing at the installed backend/models. From backend, set RUN_LOCAL_MODEL_TESTS=1 to run tests/test_local_model_integration.py explicitly.

The basic browser QA workspace uses a 512 MB worker limit and 128 MB scheduling threshold for tiny fixtures because Chrome drove this computer below the normal 1 GB free-memory reserve. Actual model loading failed under that artificial 512 MB cap; real inference tests use the normal 3,072 MB cap with a test-only 128 MB reserve. These test UI/API behavior; they are NOT production-resource or 1,000-page OCR qualification. Production defaults remain 3 GB worker / 1 GB reserve.

The latest full backend suite passed 433 tests with 3 intentional skips and 6 dependency warnings on 2026-09-24 (213.76 seconds). TypeScript/Vite compilation, 15 frontend draft-helper tests and 15 synthetic browser delayed-response/draft checks pass. The newest live workflow paused below the normal 1,024 MB reserve; successful inspection, model inference and final export were not rerun in this cycle. Live original retention, retry, restart recovery, authentication, backup checksums and new-directory restore passed. See IMPLEMENTATION-VALIDATION.md for dated scope and remaining qualifications.

To run the pure frontend draft tests from the project root, bundle `frontend/tests/region-draft.test.ts` with the installed esbuild (`--bundle --platform=node --format=esm --outfile=tmp/region-draft.test.mjs`), then run `node --test tmp/region-draft.test.mjs`. The separate `workspace-race-harness.tsx` uses the actual workspace with synthetic transport; its delayed-response, approval-failure and storage-quota controls exercise browser behavior without a PDF worker. These fixtures are separate from normal-resource end-to-end testing.

## Backup, restore and operation

Use Back up workspace to produce a SQLite backup plus original/export artifacts and SHA-256 manifest. Stop the application before switching restored data directories. Run `python -m tools.restore_workspace <backup.zip> <new-directory>` from backend. Restore rejects existing destinations, traversal, checksum mismatch and database corruption. Verification and database path updates happen in a temporary sibling directory before atomic publication. Set DATA_DIR to the restored directory when starting the backend; restore does not overwrite live data. Unsaved browser region/grid/review-note drafts are not part of server backups; save or approve them first.

Passwords stay in process memory and must be re-entered after restarting. Original source previews and orphaned artifacts are rebuildable; they are not authoritative. Do not manually edit the database or model files during a job.

## Remaining release gates

The 160-page development corpus and separate 400-page/80-document bilingual acceptance corpus have not been assembled or professionally reviewed. No measured translation accuracy, legal certification or universal fidelity guarantee exists. General scan splitting/grid correction, cross-page tables/footnotes and many-to-many context, Docling/Paddle qualification, a self-contained signed Windows installer, and complete multilingual coverage remain future implementation/qualification work. Native ruled-table review, bounded manual grids and static AcroForm review are implemented within the boundaries above. See task.md for the exact completed frontier and test findings.

## Independent PDF.js qualification

`frontend/tests/verify-pdf-text.mjs` tests local PDFs with Node 24 and PDF.js 6.3.289. This is a development qualification tool, not a runtime dependency of the Python app. Install the pinned reader into a separate ignored directory with `npm install --prefix tmp/pdfjs-qa pdfjs-dist@6.3.289`. Run `node frontend/tests/verify-pdf-text.mjs <PDF> <expectations.json> tmp/pdfjs-qa/node_modules/pdfjs-dist/legacy/build/pdf.mjs`. The expectations file accepts `occurrences` (objects with text/count), `forbidden` characters, optional `full_text`, and `render_first_page`. Comparisons collapse line breaks but do not remove spaces inside incorrectly split words. Results and optional PNG are written beside the local test PDF. Do not use unreviewed accuracy scores or duplicate pages as independent acceptance evidence.
