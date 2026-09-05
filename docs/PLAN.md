# Build and deployment plan

## First release

The product has three focused workflows:

1. Upload a born-digital PDF and read a user-selected page range without changing the source.
2. Edit the extracted text as a separate draft, and translate that draft to English or Hindi.
3. Select a word or phrase and retrieve its meaning.

The source PDF remains immutable. Arbitrarily editing text *inside* an existing PDF is not dependable: PDFs contain positioned glyphs, not reusable paragraphs. A later export feature should generate a new annotated PDF after explicit confirmation.

## Structure and SOLID boundaries

| Location | Responsibility |
| --- | --- |
| `frontend/src` | Screens, user actions, and API client; no PDF parsing. |
| `backend/app/api` | HTTP validation and response shapes only. |
| `backend/app/services` | Use cases: document reading, translation, definitions. |
| `backend/app/infrastructure` | Replaceable disk storage, PDF extraction, external API adapters. |
| `backend/tests` | Core flow tests. |

Routes depend on services; services depend on narrow adapters. Therefore a local upload directory can become private S3/R2 storage, PyMuPDF can become OCR, and LibreTranslate can become another provider with limited changes.

## Core flow

```
Browser -> POST /documents -> private temporary file -> PDF validation
Browser -> GET /documents/{id}/pages -> extraction service -> text draft
Browser -> POST /translate -> configured translator -> translated draft
Browser -> POST /definitions -> dictionary provider -> definitions
```

The document id is a UUID. Add authentication and document ownership checks before multi-user use.

## Dry-run checks

1. Upload a small text PDF: API returns a UUID, filename, and page count.
2. Read pages 1–2: returned text contains exactly those pages.
3. Request an invalid range: API returns 422.
4. Edit displayed text: only browser draft changes; the PDF file is never written again.
5. Configure LibreTranslate and test English → Hindi and Hindi → English.
6. Select an English word and test definition lookup; empty results are handled.

## Vercel + Render

Frontend: import repository in Vercel, set Root Directory to `frontend`, and set `VITE_API_BASE_URL=https://<render-service>.onrender.com`.

Backend: create a Render Web Service with Root Directory `backend`, build command `pip install -r requirements.txt`, and start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Set `ALLOWED_ORIGINS` to the exact Vercel domain.

## Security and scale before public launch

- Require login and enforce document ownership on every document route.
- Keep storage private, encrypted, with signed URLs and a deletion policy.
- Validate extension, PDF header, size, page count, and rate-limit users.
- Malware-scan files and parse untrusted PDFs in constrained workers.
- Keep provider keys server-side; use HTTPS, strict CORS, CSP/security headers, and avoid logging document text.
- Free host request limits and ephemeral disks mean “any size” is not possible. Move big uploads to direct private S3/R2 signed uploads and run extraction/OCR as background jobs with progress polling.
- Add OCR (Tesseract or a managed provider) for scanned Hindi/English PDFs; native extraction alone only handles selectable text.
