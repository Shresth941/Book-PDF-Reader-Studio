from pathlib import Path
from uuid import UUID
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from app.api.schemas import BlobDocumentRequest, BlobPagesRequest, DefinitionRequest, ExportRequest, TranslateRequest
from app.core.config import settings
from app.infrastructure.pdf_reader import PyMuPdfReader
from app.infrastructure.storage import LocalDocumentStorage, VercelBlobDocumentStorage
from app.services.document_service import DocumentService
from app.services.export_service import ExportService
from app.services.language_service import DictionaryService, LibreTranslateService

reader = PyMuPdfReader()
documents = DocumentService(LocalDocumentStorage(settings.upload_dir, settings.max_upload_bytes), reader)
blob_documents = VercelBlobDocumentStorage(settings.max_upload_bytes or 25 * 1024 * 1024)
translator = LibreTranslateService(settings.libretranslate_url, settings.libretranslate_api_key)
dictionary = DictionaryService(translator)
exporter = ExportService()
app = FastAPI(title="Private PDF Reader API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

@app.get("/api/health")
def health(): return {"status": "ok"}

@app.post("/api/documents", status_code=201)
async def upload_document(file: UploadFile = File(...)):
    identifier, filename, pages = await documents.upload(file)
    return {"id": str(identifier), "filename": filename, "pageCount": pages}

@app.get("/api/documents/{identifier}/pages")
def read_pages(identifier: UUID, start: int = Query(ge=1), end: int = Query(ge=1)):
    try: return {"pages": documents.pages(identifier, start, end)}
    except ValueError as error: raise HTTPException(422, str(error)) from error

@app.post("/api/documents/blob", status_code=201)
async def register_blob_document(payload: BlobDocumentRequest):
    content = await blob_documents.read(payload.blob_url)
    try:
        pages = reader.page_count_bytes(content)
    except Exception as error:
        raise HTTPException(415, "The uploaded file is not a valid PDF.") from error
    if not pages:
        raise HTTPException(422, "The PDF has no pages.")
    return {
        "id": payload.blob_url,
        "filename": Path(payload.filename).name,
        "pageCount": pages,
    }


@app.post("/api/documents/blob/pages")
async def read_blob_pages(payload: BlobPagesRequest):
    content = await blob_documents.read(payload.blob_url)
    try:
        return {"pages": reader.extract_bytes(content, payload.start, payload.end)}
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/translate")
async def translate(payload: TranslateRequest):
    return {
        "text": payload.text if payload.source == payload.target else await translator.translate(
            payload.text, payload.source, payload.target, payload.document_title,
        )
    }

@app.post("/api/definitions")
async def definition(payload: DefinitionRequest):
    return {
        "definitions": await dictionary.define(
            payload.term.strip(), payload.language, payload.context,
            payload.document_title, payload.document_id,
        )
    }

@app.post("/api/exports")
async def export_draft(payload: ExportRequest):
    content, media_type, extension = exporter.build(payload.text, payload.format)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="pdf-reader-draft.{extension}"'},
    )
