# Simple reader validation — 17 November 2026

## User-visible changes

The local app now opens in a simple reader. PDF import automatically extracts the first page. A full-page reading area offers extracted text, translation, and paired comparison, alongside a zoomable original. Page navigation, remaining-page extraction, processing progress, pause/resume, and editable text are visible without exposing review internals.

The source-language selector and explicit text-check checkbox replace the need to approve every ordinary paragraph manually before generating a draft. This only records the user's explicit source review; translation and completeness approvals remain unchanged. Tables, forms, overlapping OCR, mixed-language passages, and final PDF approval retain their detailed-review workflow.

For image-only pages, recognized text is shown as a suggestion. The user explicitly adopts it for editing, with the original recognition evidence retained and source approval still false. Unavailable OCR or model capabilities are explained. Current-page extracted text can be downloaded without translation. Saved document review copies support PDF, Word and TXT, with readable and original-plus-translation PDF layouts.

Browser drafts are recovered after reload. Unsaved edits prevent page/document changes, imports, downloads and mode switches. Failed saves retain remaining draft values. Detailed review preserves the selected document when switching modes. The page layout was inspected at desktop and 390px mobile widths.

`Open-PDF-Reader.cmd` provides a double-click launcher. `Start-Local.ps1` rebuilds from current source through Node when available; `-SkipBuild` explicitly reuses the build. The README now documents the current local app rather than leading readers through the retired workflow.

## Checks actually run

| Check | Result |
|---|---|
| TypeScript and Vite production build | Passed |
| Backend suite with real model and OCR checks enabled | **437 passed, 1 skipped**, 106.87 seconds |
| Draft-restoration helper suite | **15 passed** |
| New simple-reader browser suite | Passed all 20 named checks |
| Existing detailed-review browser workflow | Passed |
| Table/form browser workflow | Passed |
| Synthetic memory-recovery browser workflow | Passed |
| Startup PowerShell syntax | Passed |
| Staged credential-pattern and sensitive-path check | No matches |

The skipped backend test is the opt-in legacy hosted/live-provider pipeline. Six existing dependency deprecation warnings remain. Both model directions and both OCR languages were exercised with small synthetic documents. The OCR test deliberately confirms suggestions remain unapproved until adopted and reviewed. The existing detailed-review browser assertion was updated to the current draft-recovery wording.

The new browser checks cover invalid file extension, automatic first-page extraction, byte-identical original retention, explicit review before translation, extracted-text download, remaining-page extraction, unsaved navigation guards, draft recovery, persistence, same-language guidance, real translation, no automatic meaning approval, comparison/zoom, PDF/Word/TXT review-copy downloads, blocked unreviewed final downloads, mode handoff, failed-save recovery, missing models, mobile layout, and scan recognition adoption. Browser scripts assert no page exceptions or external requests.

## Test isolation and reproduction

Tests used separate QA databases on ports 8001 and 8002, not the user's main database on port 8000. Detailed-review missing-model tests use an empty model directory. The simple-reader suite uses installed real models and Tesseract. The installed OCR executable is UB Mannheim Tesseract 5.4.0; Hindi, English and orientation language data are supplied from the project's ignored local model directory. Installation is local to this computer and is not bundled into Git.

From `backend`, set `RUN_LOCAL_MODEL_TESTS=1` and `RUN_LOCAL_OCR_TESTS=1`, then run `.venv-local/Scripts/python.exe -m pytest tests -q`. From `frontend`, set `REVIEW_URL` to the isolated appropriate server and run `node tests/reader-browser.mjs`, `node tests/review-browser.mjs`, `node tests/structure-browser.mjs`, or `node tests/memory-browser.mjs`. See LOCAL-DEVELOPMENT.md for QA configuration and draft helper tests.

Only synthetic fixtures under `frontend/tests/fixtures` are committed. PDFs are marked binary to preserve their original byte offsets through Git checkout. Runtime documents, models, secrets, logs, test output, and built bundles are ignored.

## Limits

This is tested local usability and runtime behavior, not a universal PDF, translation-quality, or legal-accuracy certification. Large-book throughput, arbitrary complex scans, professional bilingual acceptance, fresh-machine installation, hosted deployment, and live cloud inference remain unqualified. The hosted interface is retained separately; this change makes the simple reader the default local interface. Publishing the repository does not deploy it.
