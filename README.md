# PDF Reader Studio

A local PDF reader for English and Hindi. Open a PDF, check its extracted text, translate a page, and download your work. The original PDF stays unchanged.

Extracted text and translations appear on a single editable page, with margins and placement based on the original PDF. Click text to edit and use **Page zoom** to enlarge both views. A small ↕ mark identifies text that exceeds its original space; focus that passage to scroll through it. Font substitution and longer translations can prevent an exact visual match.

## Start on this computer

Double-click **Open-PDF-Reader.cmd**, or run `./Start-Local.ps1 -OpenBrowser` from PowerShell. Open http://127.0.0.1:8000. Keep the server window running while you use the app.

Startup rebuilds the frontend when Node.js is available, so source changes appear on the next launch. `-SkipBuild` reuses the existing build. If Node.js is unavailable, startup can use an existing build with an explicit warning.

## Everyday workflow

1. **Open PDF.** The first page is extracted automatically. Use **Read all pages** for the rest of a document; progress can be paused and resumed.
2. **Read and edit.** Keep the original beside the text, zoom the original, or hide it for more reading space. Switch between **Extracted text**, **Translation**, and **Compare text**. Save edits before changing pages.
3. **For scans:** review the recognized-text suggestion and choose **Use recognized text**. Correct OCR mistakes against the original. Recognition evidence is retained and nothing is automatically approved.
   Supported XpressChanak Hindi text is decoded directly into editable Unicode, avoiding slow OCR and its spelling/number errors. Unmapped glyphs and other legacy fonts still use OCR. For an older import, select **Refresh Hindi text recognition**. Saved corrections are preserved; **Updated Hindi text available** lets you compare and explicitly replace an older transcription. Devanagari pages suggest Hindi → English; check the language before translating.
   See [Hindi extraction architecture and scenarios](docs/HINDI-EXTRACTION.md) and [validation results](docs/HINDI-VALIDATION.md).
4. **Translate.** Choose the source and target languages, confirm you checked the page's text and source language, then select **Translate page**. All editable passages must share the selected source language. Use Detailed review for mixed-language pages, tables, and forms.
5. **Download.** Download the current page's extracted text directly, or prepare a document review copy as PDF, Word, or text. PDF offers clean readable text or original pages plus translation. Incomplete content remains marked as incomplete.

**Detailed review** contains table/form tools, missing-region repair, terminology, per-passage languages, source/translation approval, page-completeness checks, and final PDF layout approval. Switching modes keeps the selected document. Unsaved work must be saved or discarded before switching.

Translations are draft assistance, not certified translations. Check names, numbers, terminology, and meaning. Generated translations never automatically receive meaning or completeness approval.

## New-computer setup

- Python 3.12 and Node.js with npm are required for setup. The checked environment uses Python 3.12.14 and Node 24.
- Run `./Setup-Local.ps1 -PythonExecutable <path-to-python-3.12> -InstallOcrData -InstallTranslationModels`.
- For scanned documents, install the Tesseract executable separately. The project downloads Hindi/English OCR language data; language data alone is not the OCR engine. See the [upstream Windows installation guidance](https://tesseract-ocr.github.io/tessdoc/Installation.html).
- On Windows, Hindi PDF output uses Nirmala fonts. Model downloads are explicit setup operations; normal local inference runs offline.

Installed virtual environments, model files, private documents, and `.env` secrets are deliberately not included in Git. Do not expect a copied virtual environment from another computer to work.

## Storage and privacy

FastAPI serves the React application and review API from the same loopback process. SQLite review records, original documents, artifacts, and backups are stored under `backend/local-data`; local models are under `backend/models`. Both directories are ignored by Git.

Use **Back up my PDFs** to download a consistent workspace backup. Unsaved browser drafts are not included: save first. Restore always targets a new directory. See [local development and recovery](docs/LOCAL-DEVELOPMENT.md).

Local mode disables the old hosted translation/dictionary routes. Optional cloud assistance is separate, disabled unless configured, and requires explicit per-request approval. No cloud processing is necessary for the default reader.

## Verification

From `backend`:

```powershell
.venv-local/Scripts/python.exe -m pytest tests -q
# Optional real installed-runtime smoke tests:
$env:RUN_LOCAL_MODEL_TESTS = '1'
$env:RUN_LOCAL_OCR_TESTS = '1'
.venv-local/Scripts/python.exe -m pytest tests -q
```

From `frontend`:

```powershell
npm run build
# Use a separate server/data directory and installed models, never the user's port 8000:
$env:REVIEW_URL = 'http://127.0.0.1:8002'
npm run test:reader
```

The existing detailed-review browser suite uses a separate QA server with an empty model directory: `REVIEW_URL=http://127.0.0.1:8001 npm run test:browser` (set the environment variable using your shell's syntax). Table/form, memory-recovery, and real-model browser suites are also in `frontend/tests`. Fixtures are synthetic.

See [the usability release checks](docs/USABILITY-VALIDATION.md) for the actual checks and limits of this release. These tests do not establish universal PDF support, professional translation accuracy, or large-book performance.

## Code and deployment

- `frontend/src/ReaderWorkspace.tsx`: simple reader and detailed-review handoff.
- `frontend/src/ReviewWorkspace.tsx`: specialist review workspace.
- `backend/app/api/review_routes.py`: local review API.
- `backend/app/infrastructure/review_store.py`: transactional review storage.
- `backend/app/infrastructure/bounded_worker.py`: bounded PDF/OCR/model processes.
- `backend/app/services/review_jobs.py`: resumable extraction/translation jobs.
- `backend/app/services/review_export.py`: immutable review exports and final gates.

The repository also retains the older hosted reader and separate hosted implementation. The new simple reader is the default **local** application. Publishing code to GitHub does not deploy it. The root Vercel configuration intentionally blocks direct workspace deployment; use the documented preparation workflow in [DEPLOYMENT.md](docs/DEPLOYMENT.md).
