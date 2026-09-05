# PDF Reader Workspace

For GitHub publishing, Vercel setup, required account access, and environment
variables, see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

This project is a private-by-default PDF reading and language utility application. It allows a user to upload a PDF file, read specific page ranges, edit the extracted text, translate content, and look up word definitions. The application is split into a Python FastAPI backend and a React + Vite frontend.

## Project goal

The main idea behind this workspace is to create a simple but practical PDF workflow that keeps the reading process private and focused. Instead of depending on a heavy external document platform, the project gives you a local application flow:

- Upload a PDF
- Extract text from chosen page ranges
- Review and edit the extracted content
- Translate between languages
- Look up word definitions
- Use the system locally in a simple browser interface

This is useful for students, researchers, translators, or anyone working with documents that need careful reading and selective extraction.

---

## What has been built so far

The current version includes:

- A FastAPI backend for document handling and text operations
- PDF extraction logic using PyMuPDF
- A document upload endpoint and page-range reading endpoint
- Translation support using a LibreTranslate-style service layer
- Dictionary/definition lookup support
- A React frontend with Vite for local development
- Local file-based document storage for uploaded PDFs
- Basic CORS configuration for the frontend to call the backend

---

## Product understanding

This project is not just a PDF viewer. It is more like a document processing workspace.

The real flow is:

1. A user uploads a PDF.
2. The backend saves the file in an upload directory.
3. The PDF reader extracts text from the selected page range.
4. The frontend displays the extracted content.
5. The user can read, update, or revise the text.
6. Translation and dictionary services can run on selected content or words.

This means the system is designed for reading and editing rather than simply displaying the original PDF pages.

### Why this project matters

This type of application solves a common real-world problem: PDF content is often hard to reuse, edit, or translate without copying text manually. This project reduces that friction by turning PDF content into a more readable and usable text workflow.

It is especially useful when working with:

- academic notes and research articles
- scanned or long-form PDF documents
- bilingual reading workflows
- learning material where word definitions help comprehension
- documents that need selective content extraction instead of full-page viewing

The project is intentionally simple and focused on the core workflow rather than trying to become a full corporate document management system.

---

## Architecture overview

### Backend

The backend is implemented in Python using FastAPI.

Core pieces:

- app.main: application entry point and route registration
- app.api.schemas: request and response schemas
- app.core.config: environment settings and CORS configuration
- app.infrastructure.pdf_reader: PDF extraction logic
- app.infrastructure.storage: local file storage abstraction
- app.services.document_service: processing and document management
- app.services.language_service: translation and dictionary logic

Each layer has a clear responsibility:

- The API layer receives requests from the frontend
- The service layer contains processing logic and application rules
- The infrastructure layer handles storage and PDF parsing
- The config layer defines environment-driven settings such as upload limits and allowed origins

This clean separation makes the project simpler to test, maintain, and extend.

### Frontend

The frontend is a React + TypeScript app with Vite.

Main pieces:

- frontend/src/main.tsx: app entry point
- frontend/src/api.ts: API wrapper for backend calls
- frontend/src/styles.css: styling
- frontend/index.html: Vite app shell

The frontend is intentionally lightweight. It does not contain heavy business logic; instead, it sends requests to the backend and renders the responses. This keeps the UI straightforward and reduces complexity.

### Request flow in practice

A simple user flow works like this:

1. The frontend opens the app in the browser.
2. The user selects a PDF.
3. The frontend calls the upload endpoint.
4. The backend stores the uploaded file and creates a document record.
5. The frontend requests text from a page range.
6. The backend reads the PDF and returns the extracted content.
7. The frontend displays it as readable text.
8. The user can then translate text or look up a word definition.

This flow keeps the app easy to reason about and update.

---

## Feature list

### PDF reading

- Upload PDF files
- Read content from selected start and end page numbers
- Extract text from PDF pages
- Focus on text extraction rather than full PDF rendering

### Translation support

- Translate text from one language to another
- Works with text-based API requests
- Useful for English/Hindi or bilingual reading workflows

### Definition lookup

- Query the meaning of a word or term
- Useful while reading a document in a second language
- Helps improve comprehension of difficult vocabulary

### Editing workflow

- Extracted content can be treated as a user-editable text layer
- The original PDF remains separate from the readable text copy
- Lets the user take notes, compare translations, or rewrite text more clearly

### Security and privacy approach

- Local document storage
- Private-by-default philosophy
- Keeps document processing within the local app environment
- Avoids unnecessary dependence on third-party processing for every use case

### Why the project is useful for learning

This is a strong beginner-to-intermediate full-stack project because it combines:

- frontend development in React
- backend API design in FastAPI
- file handling and storage
- PDF parsing and text extraction
- service-based application design
- local environment configuration and troubleshooting

It gives a realistic view of how a full-stack app is built without being too large or overly complex.

---

## Current project structure

```text
PDF Reader/
├── README.md
├── docs/
│   └── PLAN.md
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── api/
│   │   │   └── schemas.py
│   │   ├── core/
│   │   │   └── config.py
│   │   ├── infrastructure/
│   │   │   ├── pdf_reader.py
│   │   │   └── storage.py
│   │   └── services/
│   │       ├── document_service.py
│   │       └── language_service.py
│   ├── tests/
│   │   └── test_pdf_reader.py
│   ├── requirements.txt
│   └── uploads/
├── frontend/
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   └── src/
│       ├── api.ts
│       ├── main.tsx
│       └── styles.css
└── pdf_dryrun_analysis.py
```

---

## Technology stack

### Backend

- Python
- FastAPI
- Uvicorn
- PyMuPDF
- Pydantic Settings
- HTTPX
- Python multipart handling
- Local upload storage

### Frontend

- React
- TypeScript
- Vite
- HTML + CSS
- Fetch-based API communication

### Why these technologies

- FastAPI was chosen because it is fast, modern, and straightforward for APIs
- PyMuPDF is useful for extracting text from PDF documents
- React and Vite provide a lightweight and fast frontend development workflow
- The combination keeps the project easy to run locally while still matching a production-style stack closely enough for learning and practice

### Practical use case

This stack is suitable for a tool that helps a user read a PDF in a more structured and readable way, especially when they need translation or language support while studying.

---

## Local setup instructions

### Backend setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

If the default Python path on Windows is not working, use the actual installed environment path:

```powershell
cd backend
"C:\Users\SHRESTH SINGH\anaconda3\python.exe" -m venv .venv-fixed
.\.venv-fixed\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-fixed\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend setup

```powershell
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

---

## API endpoints

### GET /api/health

Returns whether the backend is running.

Example response:

```json
{
  "status": "ok"
}
```

### POST /api/documents

Uploads a PDF file and returns document metadata.

The frontend sends a file as multipart form data and receives a document identifier, filename, and page count.

### GET /api/documents/{identifier}/pages

Gets extracted text for a selected page range.

This is the main document-reading endpoint. It allows the user to choose page boundaries and retrieve the text data for those pages.

### POST /api/translate

Translates supplied text.

This endpoint is designed for text processing and is useful in bilingual reading and learning workflows.

### POST /api/definitions

Returns dictionary-style definitions for a term.

This is helpful when the user highlights or searches for a word and wants quick meaning lookup while reading.

### Example request flow

1. Upload a PDF
2. Receive document ID
3. Request text for pages 1 to 5
4. Read the extracted content
5. Send selected text to translate
6. Search for term definitions if needed

This is the core mental model behind the app.

---

## Ports used

- Backend: http://localhost:8000
- Frontend: http://localhost:5173

---

## Windows troubleshooting notes

During setup, a few environment issues appeared on this machine that are worth documenting:

- PowerShell blocked `.venv` activation scripts because of execution policy restrictions
- `python` did not resolve correctly in some terminal sessions
- Some terminals were using a stale or broken Python configuration
- The working interpreter had to be located explicitly from the installed Anaconda Python path
- Different terminals had inconsistent PATH and environment behavior

These were environment issues, not application bugs.

### What this teaches

This is a common challenge when working with local full-stack projects on Windows. Even when the codebase is correct, the execution environment can still cause startup failures. This is why checking the runtime environment, venv state, PATH values, and execution policy is important before debugging the app itself.

### Recommended debugging checklist

If the project fails to start on Windows, use this checklist:

1. Confirm Python exists and can be executed
2. Verify the correct virtual environment is active
3. Check whether PowerShell execution policy is blocking scripts
4. Confirm package installation completed successfully
5. Verify the backend is running on the expected port
6. Verify the frontend dev server started with Vite
7. Confirm the browser is calling the correct localhost URLs

This is a practical troubleshooting habit that helps with local app development.

---

## Development notes

This project is a good example of a small full-stack application where logic is separated clearly:

- API layer handles external interactions
- Services handle business logic
- Infrastructure handles storage and PDF parsing
- Frontend focuses on UI and API consumption

This structure is important because it keeps the codebase easy to scale and easier to understand as the app grows.

Possible future improvements include:

- drag-and-drop upload UI
- page thumbnails and page selection controls
- richer translation language selection
- dictionary integration with a real service
- authentication and document history
- a user dashboard for uploaded PDFs
- search or keyword highlighting inside extracted text
- multi-document comparison or reading mode

### Why this matters for future growth

The current structure is a good base for adding more features without rewriting the entire app. A future developer can add new features with minimal disruption if they preserve the separation between API, service, and infrastructure layers.

---

## Summary

This PDF reader project is designed to support reading, editing, translation, and definition lookup in a local, privacy-conscious environment. The backend handles document and language logic, while the frontend provides a simple interface for uploading and reviewing content. The app is structured for further expansion and is a solid foundation for a practical document workspace.

The work completed so far includes the project structure, backend API, PDF extraction logic, frontend app shell, and basic local startup logic. With the correct Python runtime path and local setup, the application is ready to be used and extended.

### Final understanding

If you look at the project as a whole, it is a small but realistic full-stack application that solves a meaningful problem: converting PDF content into a more readable, editable, and translated text workflow.

It combines:

- backend processing
- frontend interaction
- security/privacy-minded design
- utility-focused reading experience
- language support for learning and translation

This makes it a useful project both for learning and for building a practical personal document tool.

See [docs/PLAN.md](docs/PLAN.md) for the architectural and deployment planning notes.
