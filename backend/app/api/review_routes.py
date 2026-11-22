from __future__ import annotations
import asyncio
import hashlib
import json
from pathlib import Path
import secrets
import shutil
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict

from app.core.review import ReviewError, checked_revision
from app.infrastructure.bounded_worker import BoundedWorker, memory_status
from app.infrastructure.review_store import ReviewStore, encode, now
from app.infrastructure.structured_pdf import tesseract_path
from app.services.local_translation import capabilities
from app.services.review_jobs import JobRunner
from app.services.approved_cloud import ApprovedCloud
from app.services.review_export import PDF_VERIFICATION_VERSION


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    reviewer: str = Field(default="Local reviewer", min_length=1, max_length=120)


class RegionEdit(Revision):
    source: str = Field(max_length=200000)
    translation: str = Field(max_length=200000)
    source_language: Literal["en", "hi", "uncertain", "mixed"]
    disposition: Literal["translate", "preserve", "already_target", "decorative", "unreadable", "excluded"]
    contains_text: bool
    reason: str = Field(default="", max_length=4000)
    kind: Literal["paragraph", "heading", "footnote", "caption", "image", "manual", "page", "list", "formula", "table_cell"] = "paragraph"
    form_review_note: str = Field(default="", max_length=4000)


class TableReview(Revision):
    action: Literal["adopt", "approve_structure", "not_table"]
    note: str = Field(min_length=10, max_length=4000)


class TableMerge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row: int = Field(ge=0, le=39, strict=True)
    column: int = Field(ge=0, le=19, strict=True)
    row_span: int = Field(ge=1, le=40, strict=True)
    column_span: int = Field(ge=1, le=20, strict=True)


class ManualTable(Revision):
    x_edges: list[Annotated[float, Field(strict=True, allow_inf_nan=False)]] = Field(min_length=2, max_length=21)
    y_edges: list[Annotated[float, Field(strict=True, allow_inf_nan=False)]] = Field(min_length=2, max_length=41)
    merges: list[TableMerge] = Field(default_factory=list, max_length=200)
    replace_region_ids: list[UUID] = Field(max_length=10000)
    replace_table_id: UUID | None = None
    note: str = Field(min_length=10, max_length=4000)


class FormPreviewRequest(Revision):
    document_revision: int = Field(ge=1)


class FormBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    widget_xref: int = Field(gt=0, strict=True)
    region_id: UUID | None


class FormRefreshRequest(FormPreviewRequest):
    preview_id: UUID
    bindings: list[FormBinding] = Field(max_length=2000)
    note: str = Field(min_length=10, max_length=4000)


class AddRegion(Revision):
    bbox: tuple[float, float, float, float]


class ReadingOrder(Revision):
    region_ids: list[UUID] = Field(max_length=10000)


class ManualPage(Revision):
    reason: str = Field(min_length=10, max_length=4000)


class Approval(Revision):
    kind: Literal["coverage", "source", "translation"]
    region_id: UUID | None = None
    acknowledgements: dict[str, str] = Field(default_factory=dict)


class JobRequest(BaseModel):
    kind: Literal["extract", "translate"]
    start: int = Field(ge=1)
    end: int = Field(ge=1)


class Context(Revision):
    target: Literal["en", "hi"]
    domain: str = Field(default="general", max_length=120)
    jurisdiction: str = Field(default="", max_length=200)


class GlossaryEntry(Revision):
    source: str = Field(min_length=1, max_length=1000)
    target: str = Field(min_length=1, max_length=1000)
    sense: str = Field(min_length=1, max_length=2000)
    reference: str = Field(min_length=1, max_length=2000)
    scope: Literal["document_definition", "project", "domain", "general"] = "document_definition"
    direction: Literal["en-hi", "hi-en"]


class ExportRequest(Revision):
    format: Literal["pdf", "docx", "txt"]
    mode: Literal["readable", "faithful", "companion"] = "readable"


class LayoutApproval(BaseModel):
    page: int = Field(ge=1)
    reviewer: str = Field(min_length=1, max_length=120)
    confirmed: Literal[True]


class Password(BaseModel):
    password: str = Field(max_length=1000)


class CloudPrepare(Revision):
    page: int = Field(ge=1)
    region_ids: list[UUID] = Field(min_length=1, max_length=20)


class CloudApproval(BaseModel):
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    public_nonpersonal: Literal[True]
    reviewer: str = Field(min_length=1, max_length=120)


class ReviewAPI:
    def __init__(self, settings):
        self.settings = settings
        self.store = ReviewStore(settings.data_dir)
        self.worker = BoundedWorker(settings)
        self.runner = JobRunner(self.store, self.worker, settings)
        self.cloud = ApprovedCloud(self.store, settings)
        self.token = secrets.token_urlsafe(32)
        self.router = APIRouter(prefix="/api/v2")
        self.register()

    def original(self, identifier):
        self.store.document(str(identifier))
        return self.store.root / "originals" / f"{identifier}.pdf"

    def export_record(self, identifier):
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM exports WHERE id=?", (str(identifier),)).fetchone()
            if not row:
                raise ReviewError("export_not_found", "Export not found.", 404)
            return {**dict(row), "body": json.loads(row["body"])}

    def public_export(self, record):
        current = self.store.document(record["document_id"])
        body = record["body"]
        blockers = list(body["blockers"])
        if record["format"] == "pdf" and body.get("verification_version") != PDF_VERIFICATION_VERSION:
            blockers.append("Create a new export to apply the current content and reading-order checks.")
        approved = len(body["layout_approvals"]) == body["page_count"] and body["page_count"] > 0
        complete = record["format"] == "pdf" and not blockers and approved and current["revision"] == record["revision"]
        return {"id": record["id"], "document_id": record["document_id"], "revision": record["revision"], "format": record["format"],
                "mode": record["mode"], "page_count": body["page_count"], "blockers": blockers,
                "layout_approvals": body["layout_approvals"], "current": current["revision"] == record["revision"],
                "status": "reviewed_complete" if complete else "draft_partial" if blockers else "layout_review_required"}

    def install(self, app):
        app.include_router(self.router)
        app.add_event_handler("startup", self.runner.start)
        app.add_event_handler("shutdown", self.runner.close)

        @app.exception_handler(ReviewError)
        async def review_error(request, exc):
            return JSONResponse(status_code=exc.status, content={"detail": exc.message, "code": exc.code})

        @app.middleware("http")
        async def local_boundary(request: Request, call_next):
            if self.settings.runtime_mode != "local":
                if request.url.path.startswith("/api/v2"):
                    return JSONResponse(status_code=404, content={"detail": "Local review APIs are not enabled in hosted mode."})
                return await call_next(request)
            origin = request.headers.get("origin")
            allowed = set(self.settings.origins) | {"http://localhost:8000", "http://127.0.0.1:8000"}
            host = request.url.hostname
            if host not in ("localhost", "127.0.0.1", "::1") or (origin and origin not in allowed):
                return JSONResponse(status_code=403, content={"detail": "This service accepts trusted local application requests only."})
            if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/v2/") and request.url.path != "/api/health":
                return JSONResponse(status_code=410, content={"detail": "Use the local review workspace. Legacy processing and network routes are disabled."})
            if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
                if not request.url.path.startswith("/api/v2/"):
                    return JSONResponse(status_code=410, content={"detail": "Use the local review workspace. Legacy upload, translation and dictionary network routes are disabled."})
                if not secrets.compare_digest(request.headers.get("X-Review-Token", ""), self.token):
                    return JSONResponse(status_code=403, content={"detail": "Reload the local application to establish a review session."})
            return await call_next(request)

        from app.api.body_limit import ReviewBodyLimit
        app.add_middleware(ReviewBodyLimit, max_upload_bytes=self.settings.max_upload_bytes)

    def register(self):
        router = self.router

        @router.post("/documents/{identifier}/pages/{number}/recognition")
        async def refresh_recognition(identifier: UUID, number: int, payload: Revision):
            key = str(identifier)
            doc = self.store.document(key)
            page = self.store.page(key, number)
            checked_revision(page["revision"], payload.revision)
            if doc["status"] != "ready" or page["status"] != "extracted":
                raise ReviewError("not_extracted", "Read this page before refreshing its text recognition.")
            recognition = await asyncio.to_thread(self.worker.run, "extract", {
                "path": str(self.original(identifier)), "number": number,
                "password": self.runner.passwords.get(key, ""), "document_sha256": doc["sha256"]})
            return self.store.refresh_legacy_recognition(key, number, payload.revision, recognition, payload.reviewer)

        @router.post("/documents/{identifier}/pages/{number}/form-evidence/preview")
        async def form_preview(identifier: UUID, number: int, payload: FormPreviewRequest):
            key = str(identifier)
            doc = self.store.document(key)
            page = self.store.page(key, number)
            checked_revision(doc["revision"], payload.document_revision)
            checked_revision(page["revision"], payload.revision)
            if doc["status"] != "ready" or page["status"] != "extracted":
                raise ReviewError("not_extracted", "Extract this page before refreshing its form evidence.")
            inspection = await asyncio.to_thread(self.worker.run, "inspect_forms", {
                "path": str(self.original(identifier)), "number": number, "document_sha256": doc["sha256"],
                "password": self.runner.passwords.get(key, "")})
            return self.store.prepare_form_refresh(key, number, payload.revision, payload.document_revision, inspection)

        @router.post("/documents/{identifier}/pages/{number}/form-evidence/apply")
        async def form_apply(identifier: UUID, number: int, payload: FormRefreshRequest):
            key = str(identifier)
            doc = self.store.document(key)
            checked_revision(doc["revision"], payload.document_revision)
            checked_revision(self.store.page(key, number)["revision"], payload.revision)
            verified = await asyncio.to_thread(self.worker.run, "verify_original", {
                "path": str(self.original(identifier)), "document_sha256": doc["sha256"]})
            bindings = [{"widget_xref": b.widget_xref, "region_id": str(b.region_id) if b.region_id else None} for b in payload.bindings]
            return self.store.apply_form_refresh(key, number, payload.revision, payload.document_revision,
                str(payload.preview_id), bindings, payload.note, payload.reviewer, verified["document_sha256"])

        @router.post("/documents/{identifier}/pages/{number}/tables/{table_id}/review")
        def review_table(identifier: UUID, number: int, table_id: str, payload: TableReview):
            return self.store.review_table(str(identifier), number, payload.revision, table_id,
                payload.action, payload.note, payload.reviewer)

        @router.post("/documents/{identifier}/pages/{number}/tables/manual", status_code=201)
        def manual_table(identifier: UUID, number: int, payload: ManualTable):
            body = payload.model_dump(mode="json", exclude={"revision", "reviewer"})
            return self.store.create_manual_table(str(identifier), number, payload.revision, body, payload.reviewer)

        @router.get("/session")
        def session():
            return {"token": self.token, "runtime": "local"}

        @router.get("/capabilities")
        def get_capabilities():
            return {"runtime": "local", "translation": capabilities(self.settings.models_dir),
                    "ocr": {"installed": bool(tesseract_path()), "required_languages": ["hin", "eng"]},
                    "structure": {"native": True, "manual_tables": True, "docling": False, "paddle": False,
                                  "reason": "Advanced structure/OCR models require separate local qualification."},
                    "cloud": self.cloud.capability(),
                    "limits": {"pages": self.settings.max_pages, "bytes": self.settings.max_upload_bytes,
                               "worker_memory_mb": self.settings.worker_memory_mb}}

        @router.get("/resources")
        def resources():
            return {"memory": memory_status(self.settings)}

        @router.get("/documents")
        def documents():
            return {"documents": self.store.documents()}

        @router.post("/documents", status_code=201)
        async def upload(file: UploadFile = File(...)):
            identifier = str(uuid4())
            filename = Path(file.filename or "document.pdf").name
            if not filename.lower().endswith(".pdf"):
                raise ReviewError("pdf_required", "Choose a PDF file.", 415)
            path = self.store.root / "originals" / f"{identifier}.pdf"
            digest, size = hashlib.sha256(), 0
            try:
                with path.open("xb") as stream:
                    while chunk := await file.read(1024 * 1024):
                        size += len(chunk)
                        if size > (self.settings.max_upload_bytes or 500 * 1024**2):
                            raise ReviewError("upload_limit", "This file exceeds the configured upload limit.", 413)
                        if shutil.disk_usage(path.parent).free < 32 * 1024**2:
                            raise ReviewError("disk_full", "Free disk space before importing this document.", 507)
                        stream.write(chunk)
                        digest.update(chunk)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            try:
                inspection = await asyncio.to_thread(self.worker.run, "inspect", {"path": str(path), "max_pages": self.settings.max_pages})
            except ReviewError as exc:
                inspection = {"status": "needs_attention", "message": exc.message, "code": exc.code, "pages": []}
            inspection["bytes"] = size
            return self.store.create_document(identifier, filename, digest.hexdigest(), inspection)

        @router.get("/documents/{identifier}")
        def document(identifier: UUID):
            return self.store.document(str(identifier))

        @router.post("/documents/{identifier}/inspect")
        async def inspect(identifier: UUID, payload: Password):
            key = str(identifier)
            inspection = await asyncio.to_thread(self.worker.run, "inspect", {"path": str(self.original(identifier)), "max_pages": self.settings.max_pages, "password": payload.password})
            if inspection["status"] != "ready":
                raise ReviewError("password_required", "The PDF password was not accepted.", 423)
            self.runner.passwords[key] = payload.password
            with self.store.connection(True) as db:
                current = self.store._document(db, key)
                if not current["page_count"]:
                    for info in inspection["pages"]:
                        page = {**info, "revision": 1, "status": "pending", "regions": [], "coverage_approved": False, "error": ""}
                        db.execute("INSERT INTO pages VALUES(?,?,?,?)", (key, info["number"], 1, encode(page)))
                db.execute("UPDATE documents SET status='ready',page_count=?,metadata=?,revision=revision+1 WHERE id=?", (inspection["page_count"], encode(inspection), key))
                self.store.event(db, key, "inspect", {"status": "ready"})
            return self.store.document(key)

        @router.get("/documents/{identifier}/original")
        def original(identifier: UUID):
            return FileResponse(self.original(identifier), media_type="application/pdf", filename=self.store.document(str(identifier))["filename"])

        @router.get("/documents/{identifier}/pages/{number}")
        def page(identifier: UUID, number: int):
            return self.store.page(str(identifier), number)

        @router.get("/documents/{identifier}/pages/{number}/image")
        async def page_image(identifier: UUID, number: int):
            self.store.page(str(identifier), number)
            output = self.store.root / "artifacts" / f"{identifier}-source-{number}.png"
            if not output.is_file():
                await asyncio.to_thread(self.worker.run, "render", {"path": str(self.original(identifier)), "number": number,
                    "output_path": str(output), "password": self.runner.passwords.get(str(identifier), "")})
            return FileResponse(output, media_type="image/png")

        @router.patch("/documents/{identifier}/pages/{number}/regions/{region_id}")
        def edit(identifier: UUID, number: int, region_id: UUID, payload: RegionEdit):
            return self.store.mutate_region(str(identifier), number, payload.revision, str(region_id), payload.model_dump(exclude={"revision", "reviewer"}), payload.reviewer)

        @router.post("/documents/{identifier}/pages/{number}/regions")
        def add(identifier: UUID, number: int, payload: AddRegion):
            return self.store.add_region(str(identifier), number, payload.revision, {"bbox": payload.bbox}, payload.reviewer)

        @router.post("/documents/{identifier}/pages/{number}/reading-order")
        def reorder(identifier: UUID, number: int, payload: ReadingOrder):
            return self.store.reorder_regions(str(identifier), number, payload.revision,
                [str(i) for i in payload.region_ids], payload.reviewer)

        @router.post("/documents/{identifier}/pages/{number}/approvals")
        def approve(identifier: UUID, number: int, payload: Approval):
            return self.store.approve(str(identifier), number, payload.revision, payload.kind, payload.reviewer,
                                      str(payload.region_id) if payload.region_id else None, payload.acknowledgements)

        @router.post("/documents/{identifier}/pages/{number}/manual")
        def manual(identifier: UUID, number: int, payload: ManualPage):
            with self.store.connection(True) as db:
                p = self.store._page(db, str(identifier), number)
                checked_revision(p["revision"], payload.revision)
                if not p["regions"]:
                    raise ReviewError("regions_required", "Draw and transcribe the page regions, or explicitly identify a blank page first.")
                p.update(status="extracted", coverage_approved=False, error="")
                self.store._save_page(db, str(identifier), p, "manual_page", {"reason": payload.reason, "reviewer": payload.reviewer})
            return self.store.page(str(identifier), number)

        @router.patch("/documents/{identifier}/context")
        def context(identifier: UUID, payload: Context):
            return self.store.update_context(str(identifier), payload.revision, payload.model_dump(exclude={"revision", "reviewer"}))

        @router.get("/documents/{identifier}/glossary")
        def glossary(identifier: UUID):
            return {"entries": self.store.glossary(str(identifier))}

        @router.post("/documents/{identifier}/glossary")
        def add_glossary(identifier: UUID, payload: GlossaryEntry):
            return self.store.add_glossary(str(identifier), payload.revision, payload.model_dump(exclude={"revision"}))

        @router.get("/documents/{identifier}/jobs")
        def jobs(identifier: UUID):
            return {"jobs": self.store.jobs(str(identifier))}

        @router.post("/documents/{identifier}/jobs", status_code=202)
        def enqueue(identifier: UUID, payload: JobRequest):
            count = self.store.document(str(identifier))["page_count"]
            if max(payload.start, payload.end) > count:
                raise ReviewError("page_range", f"Select pages between 1 and {count}.")
            result = self.store.enqueue(str(identifier), payload.kind, {"pages": list(range(min(payload.start, payload.end), max(payload.start, payload.end) + 1))})
            self.runner.wake.set()
            return result

        @router.post("/jobs/{identifier}/{action}")
        def control(identifier: UUID, action: Literal["pause", "resume", "cancel"]):
            result = self.store.control(str(identifier), action)
            self.runner.wake.set()
            return result

        @router.post("/documents/{identifier}/exports", status_code=201)
        async def export(identifier: UUID, payload: ExportRequest):
            snapshot = self.store.snapshot(str(identifier), payload.revision)
            export_id = str(uuid4())
            path = self.store.root / "artifacts" / f"{export_id}.{payload.format}"
            try:
                result = await asyncio.to_thread(self.worker.run, "export", {"snapshot": snapshot,
                    "source_path": str(self.original(identifier)), "output_path": str(path), "format": payload.format,
                    "mode": payload.mode, "password": self.runner.passwords.get(str(identifier), "")})
                with path.open("rb") as exported:
                    artifact_hash = hashlib.file_digest(exported, "sha256").hexdigest()
                body = {**result, "sha256": artifact_hash, "blockers": snapshot["blockers"] + result.get("compatibility_issues", []), "snapshot": snapshot, "layout_approvals": {}}
                # Existing edits do not mutate the export; stale exports cannot become final.
                with self.store.connection(True) as db:
                    db.execute("INSERT INTO exports VALUES(?,?,?,?,?,?,?)", (export_id, str(identifier), payload.revision, payload.format, payload.mode, encode(body), now()))
                    self.store.event(db, str(identifier), "export", {"id": export_id, "revision": payload.revision})
            except Exception:
                # The worker has returned/terminated and registration has not
                # committed. Clean this request's outputs on hashing or database
                # failure too, including its companion-mode temporary file.
                # Cancellation is deliberately excluded: a thread may still run.
                for owned_output in (path, path.with_suffix(".combined.pdf")):
                    owned_output.unlink(missing_ok=True)
                raise
            # Once committed, a response failure must not delete registered content.
            return self.public_export(self.export_record(export_id))

        @router.get("/exports/{identifier}")
        def export_info(identifier: UUID):
            return self.public_export(self.export_record(identifier))

        @router.get("/documents/{identifier}/exports")
        def export_list(identifier: UUID):
            with self.store.connection() as db:
                ids = [r[0] for r in db.execute("SELECT id FROM exports WHERE document_id=? ORDER BY created DESC LIMIT 20", (str(identifier),))]
            return {"exports": [self.public_export(self.export_record(i)) for i in ids]}

        @router.get("/exports/{identifier}/pages/{number}/image")
        async def export_image(identifier: UUID, number: int):
            record = self.export_record(identifier)
            if not 1 <= number <= record["body"]["page_count"]:
                raise ReviewError("page_range", "Export page not found.", 404)
            output = self.store.root / "artifacts" / f"{identifier}-preview-{number}.png"
            if not output.is_file():
                await asyncio.to_thread(self.worker.run, "render", {"path": record["body"]["path"], "number": number, "output_path": str(output)})
            return FileResponse(output, media_type="image/png")

        @router.post("/exports/{identifier}/approvals")
        def approve_layout(identifier: UUID, payload: LayoutApproval):
            with self.store.connection(True) as db:
                row = db.execute("SELECT * FROM exports WHERE id=?", (str(identifier),)).fetchone()
                if not row:
                    raise ReviewError("export_not_found", "Export not found.", 404)
                checked_revision(self.store._document(db, row["document_id"])["revision"], row["revision"])
                body = json.loads(row["body"])
                if row["format"] == "pdf" and body.get("verification_version") != PDF_VERIFICATION_VERSION:
                    raise ReviewError("export_verification_outdated", "Create a new export to apply the current content and reading-order checks.", 409)
                with Path(body["path"]).open("rb") as artifact_file:
                    if hashlib.file_digest(artifact_file, "sha256").hexdigest() != body.get("sha256"):
                        raise ReviewError("artifact_changed", "This export changed on disk. Create a new snapshot.", 409)
                if body["blockers"] or not 1 <= payload.page <= body["page_count"]:
                    raise ReviewError("content_incomplete", "Resolve source/translation issues and create a new export before final layout approval.")
                body["layout_approvals"][str(payload.page)] = {"reviewer": payload.reviewer, "at": now()}
                db.execute("UPDATE exports SET body=? WHERE id=?", (encode(body), str(identifier)))
                self.store.event(db, row["document_id"], "layout_approval", {"export": str(identifier), **payload.model_dump()})
            return self.public_export(self.export_record(identifier))

        @router.get("/exports/{identifier}/download")
        def download(identifier: UUID, final: bool = False):
            record = self.export_record(identifier)
            public = self.public_export(record)
            if final and public["status"] != "reviewed_complete":
                raise ReviewError("final_blocked", "Final download requires current content approval and every exported page's layout approval.", 409)
            if final:
                with Path(record["body"]["path"]).open("rb") as artifact_file:
                    if hashlib.file_digest(artifact_file, "sha256").hexdigest() != record["body"].get("sha256"):
                        raise ReviewError("artifact_changed", "The reviewed export changed on disk; final download is blocked.", 409)
            label = "reviewed-complete" if final else "review-copy"
            return FileResponse(record["body"]["path"], filename=f"{label}.{record['format']}")

        @router.get("/exports/{identifier}/report")
        def report(identifier: UUID):
            record = self.export_record(identifier)
            return {**self.public_export(record), "source_sha256": record["body"]["snapshot"]["document"]["sha256"],
                    "validation": {k: record["body"].get(k) for k in ("render_verified", "text_verified", "text_encoding", "text_encoding_scope", "verification_version")},
                    "content_locations": [{k: item[k] for k in ("source_page", "region_id", "spans")}
                                          for item in record["body"].get("content_map", [])]}

        @router.post("/backups", status_code=201)
        async def backup():
            from app.services.review_backup import create_backup
            result = await asyncio.to_thread(create_backup, self.store)
            return {"id": result.stem, "filename": result.name}

        @router.post("/documents/{identifier}/cloud-requests", status_code=201)
        def cloud_prepare(identifier: UUID, payload: CloudPrepare):
            return self.cloud.prepare(str(identifier), payload.page, payload.revision, [str(i) for i in payload.region_ids])

        @router.post("/cloud-requests/{identifier}/approve")
        async def cloud_approve(identifier: UUID, payload: CloudApproval):
            return await self.cloud.approve(str(identifier), payload.payload_sha256, payload.public_nonpersonal, payload.reviewer)

        @router.get("/backups/{identifier}")
        def backup_file(identifier: UUID):
            path = self.store.root / "backups" / f"{identifier}.zip"
            if not path.is_file():
                raise ReviewError("not_found", "Backup not found.", 404)
            return FileResponse(path, filename=path.name)
