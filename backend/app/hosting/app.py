"""Authenticated Vercel entrypoint. No database or background thread at import."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from starlette.staticfiles import StaticFiles

from app.api.review_routes import ReviewAPI
from app.core.review import ReviewError
from app.hosting import auth
from app.hosting.blob_storage import BlobStorage, BlobStorageError
from app.hosting.runtime import HostedWorker, hosted_memory, hosted_settings
from app.hosting.workspace import HostedWorkspace
from app.services.approved_cloud import ApprovedCloud
from app.services.local_translation import capabilities
from app.infrastructure.structured_pdf import tesseract_path

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=1000)


class UploadStart(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    size: int = Field(ge=1, le=64 * 1024**2)


class UploadFinish(BaseModel):
    ticket: str = Field(max_length=4000)


def ticket_payload(value):
    try:
        body, signature = value.rsplit(".", 1)
        if not secrets.compare_digest(auth.signature("upload:" + body), signature):
            raise ValueError()
        result = json.loads(base64.urlsafe_b64decode(body))
        UUID(result["id"])
        if result["expires"] < time.time():
            raise ValueError()
        return result
    except (ValueError, KeyError, TypeError):
        raise ReviewError("upload_expired", "This upload authorization expired. Import the file again.", 409)


@app.exception_handler(ReviewError)
async def review_error(request, exc):
    return JSONResponse({"code": exc.code, "detail": exc.message}, status_code=exc.status)


@app.exception_handler(BlobStorageError)
async def storage_error(request, exc):
    return JSONResponse({"code": "storage_unavailable", "detail": "Private document storage is unavailable or its free allowance is exhausted. Saved work has not been replaced. Retry after restoring storage availability."}, status_code=503)


@app.middleware("http")
async def private_boundary(request: Request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    allowed = {"book-pdf-reader-studio.vercel.app", os.environ.get("VERCEL_URL", ""), os.environ.get("VERCEL_BRANCH_URL", "")}
    allowed.update(filter(None, os.environ.get("REVIEW_ALLOWED_HOSTS", "").split(",")))
    if request.url.hostname not in allowed:
        return JSONResponse({"detail": "This host is not authorized."}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("origin") != f"https://{request.url.hostname}":
            return JSONResponse({"detail": "Use this website to submit changes."}, status_code=403)
    session = request.cookies.get(auth.COOKIE, "")
    authenticated = auth.valid_session(session)
    request.state.owner_session = session if authenticated else ""
    public = request.url.path in ("/api/auth/session", "/api/auth/login", "/api/health")
    if not public and not authenticated:
        return JSONResponse({"detail": "Sign in to your private workspace."}, status_code=401)
    if request.url.path.startswith("/api/v2/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if not secrets.compare_digest(request.headers.get("x-review-token", ""), auth.review_token(session)):
            return JSONResponse({"detail": "Reload the workspace to refresh your session."}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/api/health")
def health():
    return {"status": "ok", "runtime": "vercel", "processing": "bounded_queue"}


@app.get("/api/auth/session")
def session(request: Request):
    return {"authenticated": bool(request.state.owner_session)}


@app.post("/api/auth/login")
async def login(payload: Login):
    if not await asyncio.to_thread(auth.password_matches, payload.password):
        await asyncio.sleep(0.5)
        return JSONResponse({"detail": "The workspace password is incorrect."}, status_code=401)
    response = JSONResponse({"authenticated": True})
    response.set_cookie(auth.COOKIE, auth.new_session(), max_age=auth.LIFETIME, secure=True, httponly=True, samesite="strict", path="/")
    return response


@app.post("/api/auth/logout")
def logout():
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(auth.COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return response


@app.get("/api/v2/session")
def review_session(request: Request):
    return {"token": auth.review_token(request.state.owner_session), "runtime": "vercel"}


@app.get("/api/v2/resources")
def resources():
    return {"memory": hosted_memory(hosted_settings(Path("/tmp")))}


@app.get("/api/v2/capabilities")
def get_capabilities():
    settings = hosted_settings(Path("/tmp"))
    return {"runtime": "vercel", "translation": capabilities(settings.models_dir),
            "ocr": {"installed": bool(tesseract_path()), "required_languages": ["hin", "eng"]},
            "structure": {"native": True, "manual_tables": True, "docling": False, "paddle": False,
                          "reason": "Advanced structure models are not qualified in this deployment."},
            "cloud": {"available": False, "reason": "Optional external AI assistance is disabled. Recognition and draft translation run inside this Vercel deployment."},
            "limits": {"pages": 1000, "bytes": settings.max_upload_bytes, "worker_memory_mb": settings.worker_memory_mb}}


@app.post("/api/v2/uploads", status_code=201)
async def begin_upload(payload: UploadStart):
    if not payload.filename.lower().endswith(".pdf"):
        raise ReviewError("pdf_required", "Choose a PDF file.", 415)
    identifier = str(uuid4())
    body = base64.urlsafe_b64encode(json.dumps({"id": identifier, "filename": payload.filename,
        "size": payload.size, "expires": int(time.time()) + 1800}).encode()).decode()
    signed = await asyncio.to_thread(BlobStorage().signed_upload, f"pdf-review/v1/uploads/{identifier}.pdf", expires_seconds=900)
    return {**signed, "ticket": body + "." + auth.signature("upload:" + body)}


@app.post("/api/v2/uploads/complete", status_code=201)
async def complete_upload(payload: UploadFinish):
    info = ticket_payload(payload.ticket)
    def finish():
        with HostedWorkspace() as workspace:
            try:
                return workspace.store.document(info["id"])
            except ReviewError as exc:
                if exc.status != 404:
                    raise
            original = workspace.path(f"originals/{info['id']}.pdf")
            blob = workspace.blob.download_file(f"pdf-review/v1/uploads/{info['id']}.pdf", original, max_bytes=info["size"])
            if not blob or original.stat().st_size != info["size"]:
                raise ReviewError("upload_incomplete", "The upload is incomplete. Import the PDF again.", 409)
            with original.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            settings = hosted_settings(workspace.root)
            try:
                inspection = HostedWorker(settings).run("inspect", {"path": str(original), "max_pages": settings.max_pages})
            except ReviewError as exc:
                inspection = {"status": "needs_attention", "message": exc.message, "code": exc.code, "pages": []}
            inspection["bytes"] = info["size"]
            document = workspace.store.create_document(info["id"], info["filename"], digest, inspection)
            workspace.commit()
            return document
    return await asyncio.to_thread(finish)


async def dispatch_safely(store):
    from app.hosting.queue_worker import dispatch_pending
    try:
        await dispatch_pending(store)
    except Exception:
        # Queued state is already durable. The next jobs request retries dispatch.
        # Never turn a committed edit into an apparent failed edit.
        return False
    return True


def request_app(workspace, token):
    class DurableRoute(APIRoute):
        def get_route_handler(self):
            original = super().get_route_handler()
            async def execute(request):
                await asyncio.to_thread(workspace.hydrate_for, request.url.path, request.method)
                response = await original(request)
                await asyncio.to_thread(workspace.commit)
                if request.url.path.endswith("/jobs") or "/jobs/" in request.url.path:
                    dispatched = await dispatch_safely(workspace.store)
                    if not dispatched:
                        response.headers["X-Review-Queue"] = "dispatch-pending"
                if isinstance(response, FileResponse):
                    relative = Path(response.path).relative_to(workspace.root).as_posix()
                    # commit publishes new files; retrieve the committed mapping.
                    info = workspace.files.get(relative)
                    if not info:
                        with Path(response.path).open("rb") as stream:
                            digest = hashlib.file_digest(stream, "sha256").hexdigest()
                        key = f"pdf-review/v1/objects/{digest}"
                    else:
                        key = info["key"]
                    url = await asyncio.to_thread(workspace.blob.signed_download, key, expires_seconds=300)
                    return RedirectResponse(url, status_code=303, headers={"Cache-Control": "private, no-store"})
                return response
            return execute

    api = ReviewAPI.__new__(ReviewAPI)
    api.settings = hosted_settings(workspace.root)
    api.store = workspace.store
    api.worker = HostedWorker(api.settings)
    api.runner = SimpleNamespace(passwords=workspace.passwords, wake=threading.Event())
    api.cloud = ApprovedCloud(api.store, api.settings)
    api.token = token
    api.router = APIRouter(prefix="/api/v2", route_class=DurableRoute)
    api.register()
    inner = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    inner.add_exception_handler(ReviewError, review_error)
    inner.add_exception_handler(BlobStorageError, storage_error)
    inner.include_router(api.router)
    return inner


class WorkspaceEndpoint:
    async def __call__(self, scope, receive, send):
        workspace = HostedWorkspace()
        try:
            await asyncio.to_thread(workspace.__enter__)
            request = Request(scope)
            inner = request_app(workspace, auth.review_token(request.cookies.get(auth.COOKIE, "")))
            await inner(scope, receive, send)
        except (ReviewError, BlobStorageError) as exc:
            response = await (review_error(None, exc) if isinstance(exc, ReviewError) else storage_error(None, exc))
            await response(scope, receive, send)
        finally:
            await asyncio.to_thread(workspace.__exit__, None, None, None)


# A raw route keeps the original /api/v2 path for the per-request router.
from starlette.routing import Route
app.router.routes.append(Route("/api/v2/{path:path}", WorkspaceEndpoint(), methods=["GET", "POST", "PATCH", "DELETE", "PUT"]))
public = Path(__file__).resolve().parents[2] / "public"
if public.is_dir():
    app.mount("/", StaticFiles(directory=public, html=True), name="frontend")
