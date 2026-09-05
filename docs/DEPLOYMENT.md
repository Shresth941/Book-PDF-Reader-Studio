# GitHub and free Vercel deployment

## Live project

- GitHub: <https://github.com/Shresth941/Book-PDF-Reader-Studio>
- Production: <https://book-pdf-reader-studio.vercel.app>

The Vercel project is connected to the GitHub repository. Pushes to `main`
create production deployments automatically.

## Architecture

The repository uses Vercel Services to build three applications in one project:

- `frontend`: the React and Vite user interface
- `backend`: the Python FastAPI document and language API
- `blob_upload`: the Express endpoint that issues short-lived Blob upload tokens

Requests under `/api/` are routed to FastAPI, except `/api/blob-upload`, which
is routed to the Express service to generate short-lived upload authorization
for the frontend. All other requests go to the Vite frontend.

PDFs upload directly from the browser to a private Vercel Blob store. The
FastAPI Function reads the private blob only when extracting the selected page
range. This avoids the 4.5 MB Function request limit and does not rely on a
Function's temporary filesystem.

## Free-plan boundaries

The app uses Vercel's Hobby allowances. The private Blob allowance includes up
to 1 GB-month of storage, 2,000 advanced operations, and 10 GB of Blob data
transfer per month. Hobby users are not charged for additional Blob usage;
Blob access pauses when a limit is exceeded until usage becomes available
again. PDF uploads are capped at 25 MB by the upload token and backend.

Scanned-page OCR uses RapidOCR, ONNX Runtime, and headless OpenCV. Those native
dependencies make the Python Function larger than the standard 500 MB Python
bundle limit, so the project opts into Vercel Large Functions (public beta) on
Fluid compute. The setting is required for production and preview deployments.

Private uploaded PDFs remain in Blob storage until removed through the Vercel
Storage dashboard. The app does not expose a public file URL. Avoid uploading
confidential documents until user authentication and document ownership checks
are added. Translation and dictionary features can send selected text to
external language providers.

## Environment variables

Vercel automatically provides the private Blob credentials when the store is
connected to this project. Never copy those credentials into GitHub.

| Variable | Location | Purpose |
| --- | --- | --- |
| `BLOB_READ_WRITE_TOKEN` | Vercel only | Private Blob read and upload authorization |
| `VERCEL_SUPPORT_LARGE_FUNCTIONS` | Vercel Production and Preview | Set to `1` so the OCR runtime can exceed the standard Python bundle limit |
| `MAX_UPLOAD_BYTES` | Optional Vercel setting | Additional backend upload cap |
| `LIBRETRANSLATE_URL` | Optional Vercel setting | Translation provider URL |
| `LIBRETRANSLATE_API_KEY` | Optional Vercel secret | Translation provider credential |
| `VITE_API_BASE_URL` | Local frontend only | Local FastAPI origin, such as `http://localhost:8000` |

Variables beginning with `VITE_` are visible in the browser bundle and must
never contain secrets. Local `.env` files, Vercel project metadata, private
PDFs, logs, dependencies, and build output are excluded from Git.

## Local development

Run FastAPI on port 8000 and Vite on port 5173 as described in the project
README. Local development continues to use the local upload directory. The
deployed production build automatically uses same-origin `/api` routes and
private Blob uploads.

## Deployment verification

After each production deployment:

1. Check `/api/health` returns `{"status":"ok"}`.
2. Upload a synthetic PDF smaller than 25 MB.
3. Read a short page range and verify the extracted text.
4. Test text, Word, and PDF draft exports.
5. Test translation separately if an optional provider is configured.

## Official references

- [Vercel Services](https://vercel.com/kb/guide/vercel-services)
- [FastAPI on Vercel](https://vercel.com/docs/frameworks/backend/fastapi)
- [Vercel Blob private storage](https://vercel.com/docs/vercel-blob/private-storage)
- [Vercel Blob pricing](https://vercel.com/docs/vercel-blob/usage-and-pricing)
