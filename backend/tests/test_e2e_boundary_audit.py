"""Fault-injected API/job/backup boundaries. All workspaces are pytest temp data.

These tests exercise real SQLite transactions and ASGI requests, not PDF/model
quality. Worker stand-ins deliberately simulate crashes and stale responses.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.review_routes import ReviewAPI
from app.core.config import Settings
from app.core.review import ReviewError
from app.infrastructure.review_store import ReviewStore, encode, now
from app.infrastructure.structured_pdf import region
from app.services import review_backup
from app.services.review_jobs import JobRunner


@pytest.fixture
def boundary_store(tmp_path):
    store = ReviewStore(tmp_path / "workspace")
    identifier = str(uuid4())
    original = store.root / "originals" / f"{identifier}.pdf"
    # No PDF parser is used in this transactional fixture.
    original.write_bytes(b"%PDF-transaction-boundary-fixture")
    store.create_document(identifier, "fixture.pdf", review_backup.digest(original), {
        "status": "ready", "page_count": 2,
        "pages": [{"number": n, "width": 600, "height": 800} for n in (1, 2)],
    })
    return store, identifier


def start_job(store, identifier, pages=(1, 2)):
    job = store.enqueue(identifier, "extract", {"pages": list(pages)})
    with store.connection(True) as db:
        db.execute("UPDATE jobs SET status='running',attempt=1 WHERE id=?", (job["id"],))
    return store.job(job["id"])


def rewritten_archive(source, destination, change):
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    change(files)
    with zipfile.ZipFile(destination, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return destination


def test_checkpoint_rolls_back_page_revision_cursor_and_event_when_write_fails(boundary_store, monkeypatch):
    store, identifier = boundary_store
    job = start_job(store, identifier)
    page = store.page(identifier, 1)
    before_doc = store.document(identifier)
    with store.connection() as db:
        event_count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def interrupted_event(*args):
        raise sqlite3.OperationalError("injected disk write interruption")

    monkeypatch.setattr(store, "event", interrupted_event)
    result = {**deepcopy(page), "status": "extracted", "regions": [
        region("paragraph", [20, 20, 100, 70], "Do not partially publish")
    ]}
    with pytest.raises(sqlite3.OperationalError):
        store.checkpoint(job["id"], 1, 1, page["revision"], result, before_doc["revision"])
    assert store.page(identifier, 1) == page
    assert store.document(identifier) == before_doc
    assert store.job(job["id"])["cursor"] == 0
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == event_count


def test_job_failure_retains_first_checkpoint_and_resume_skips_committed_page(boundary_store):
    store, identifier = boundary_store
    calls = []

    class Worker:
        def run(self, kind, payload, cancelled):
            calls.append(payload["number"])
            if calls == [1, 2]:
                raise RuntimeError("injected child failure")
            return {"status": "extracted", "regions": [
                region("paragraph", [20, 20, 100, 70], f"Page {payload['number']}")
            ]}

    runner = JobRunner(store, Worker(), Settings(data_dir=store.root))
    job = start_job(store, identifier)
    runner.run(job)
    first = deepcopy(store.page(identifier, 1))
    paused = store.job(job["id"])
    assert paused["status"] == "paused" and paused["cursor"] == 1
    assert store.page(identifier, 2)["status"] == "pending"
    store.control(job["id"], "resume")
    with store.connection(True) as db:
        db.execute("UPDATE jobs SET status='running',attempt=attempt+1 WHERE id=?", (job["id"],))
    runner.run(store.job(job["id"]))
    assert calls == [1, 2, 2]
    assert store.page(identifier, 1) == first
    assert store.job(job["id"])["status"] == "completed"
    assert store.job(job["id"])["cursor"] == 2


@pytest.mark.parametrize("action", ["pause", "cancel", "recover"])
def test_superseded_worker_cannot_publish_after_control_or_recovery(boundary_store, action):
    store, identifier = boundary_store
    job = start_job(store, identifier)
    before = store.page(identifier, 1)

    class Worker:
        def run(self, kind, payload, cancelled):
            store.recover() if action == "recover" else store.control(job["id"], action)
            return {"status": "extracted", "regions": [region("paragraph", [20, 20, 100, 70], "Late result")]}

    JobRunner(store, Worker(), Settings(data_dir=store.root)).run(job)
    assert store.page(identifier, 1) == before
    assert store.job(job["id"])["cursor"] == 0
    assert store.job(job["id"])["status"] == ("cancelled" if action == "cancel" else "paused")


def test_restore_rebases_exports_and_pauses_job_with_new_attempt(boundary_store, tmp_path):
    store, identifier = boundary_store
    job = start_job(store, identifier)
    artifact_id = str(uuid4())
    artifact = store.root / "artifacts" / f"{artifact_id}.txt"
    artifact.write_text("Saved review copy", encoding="utf-8")
    body = {"path": str(artifact), "sha256": review_backup.digest(artifact), "layout_approvals": {}}
    with store.connection(True) as db:
        db.execute("INSERT INTO exports VALUES(?,?,?,?,?,?,?)", (
            artifact_id, identifier, 1, "txt", "readable", encode(body), now()))
    backup = review_backup.create_backup(store)
    original_hash = review_backup.digest(store.root / "originals" / f"{identifier}.pdf")
    backup_hash = review_backup.digest(backup)
    restored_root = review_backup.restore_backup(backup, tmp_path / "restored")
    restored = ReviewStore(restored_root)
    assert restored.job(job["id"])["status"] == "paused"
    assert restored.job(job["id"])["attempt"] > job["attempt"]
    assert restored.job(job["id"])["cursor"] == job["cursor"]
    with restored.connection() as db:
        saved = json.loads(db.execute("SELECT body FROM exports").fetchone()[0])
    assert Path(saved["path"]) == restored_root / "artifacts" / artifact.name
    assert review_backup.digest(Path(saved["path"])) == body["sha256"]
    assert store.job(job["id"])["status"] == "running"
    assert review_backup.digest(store.root / "originals" / f"{identifier}.pdf") == original_hash
    assert review_backup.digest(backup) == backup_hash


@pytest.mark.parametrize("tamper", ["checksum", "unsafe_path"])
def test_corrupt_or_unsafe_restore_never_creates_destination(boundary_store, tmp_path, tamper):
    store, identifier = boundary_store
    backup = review_backup.create_backup(store)

    def alter(files):
        if tamper == "checksum":
            files[f"originals/{identifier}.pdf"] += b"corrupted"
        else:
            files["../escaped.txt"] = b"untrusted"
            manifest = json.loads(files["manifest.json"])
            manifest["files"]["../escaped.txt"] = hashlib.sha256(b"untrusted").hexdigest()
            files["manifest.json"] = json.dumps(manifest).encode()

    damaged = rewritten_archive(backup, tmp_path / "damaged.zip", alter)
    destination = tmp_path / "restored"
    with pytest.raises(ReviewError) as error:
        review_backup.restore_backup(damaged, destination)
    assert error.value.code == "invalid_backup"
    assert not destination.exists()
    assert not (tmp_path / "escaped.txt").exists()


def test_restore_failure_never_exposes_partial_destination(boundary_store, tmp_path, monkeypatch):
    store, _ = boundary_store
    backup = review_backup.create_backup(store)
    destination = tmp_path / "restored"

    # The production implementation may use a final atomic rename. Inject the
    # equivalent publication failure at either mechanism without failing reads.
    original_copytree = review_backup.shutil.copytree
    original_rename = Path.rename

    def interrupted_copytree(source, target, *args, **kwargs):
        if Path(target) == destination:
            Path(target).mkdir()
            (Path(target) / "review.sqlite3").write_bytes(b"partial database")
            raise OSError("injected publication interruption")
        return original_copytree(source, target, *args, **kwargs)

    def interrupted_rename(source, target):
        if Path(target) == destination:
            raise OSError("injected publication interruption")
        return original_rename(source, target)

    monkeypatch.setattr(review_backup.shutil, "copytree", interrupted_copytree)
    monkeypatch.setattr(Path, "rename", interrupted_rename)
    with pytest.raises(OSError, match="publication interruption"):
        review_backup.restore_backup(backup, destination)
    assert not destination.exists(), "A failed restore must not leave a selectable half-workspace"


def test_restore_interrupted_after_extract_copy_cleans_only_its_staging(boundary_store, tmp_path, monkeypatch):
    store, _ = boundary_store
    backup = review_backup.create_backup(store)
    backup_hash = review_backup.digest(backup)
    destination = tmp_path / "restored"
    existing = {path.name for path in tmp_path.iterdir()}
    copy = review_backup.shutil.copyfileobj

    def interrupted_copy(source, output, *args):
        copy(source, output, *args)
        raise OSError("injected failure after extracted file copy")

    monkeypatch.setattr(review_backup.shutil, "copyfileobj", interrupted_copy)
    with pytest.raises(OSError, match="after extracted file copy"):
        review_backup.restore_backup(backup, destination)
    assert not destination.exists()
    assert {path.name for path in tmp_path.iterdir()} == existing
    assert review_backup.digest(backup) == backup_hash


def test_restore_existing_destination_and_unrelated_files_are_untouched(boundary_store, tmp_path):
    store, _ = boundary_store
    backup = review_backup.create_backup(store)
    destination = tmp_path / "existing"
    destination.mkdir()
    sentinel = destination / "my-work.txt"
    sentinel.write_bytes(b"Never overwrite this work")
    with pytest.raises(ReviewError) as error:
        review_backup.restore_backup(backup, destination)
    assert error.value.code == "restore_target_exists"
    assert sentinel.read_bytes() == b"Never overwrite this work"
    assert list(destination.iterdir()) == [sentinel]


@pytest.fixture
def boundary_api(tmp_path, monkeypatch):
    service = ReviewAPI(Settings(data_dir=tmp_path / "api"))
    monkeypatch.setattr(service.runner, "start", lambda: None)
    monkeypatch.setattr(service.runner, "close", lambda: None)
    app = FastAPI()
    service.install(app)
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
        yield service, client


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Host": "127.0.0.1.evil.example"},
    {"Host": "evil.example", "X-Forwarded-Host": "127.0.0.1"},
])
def test_untrusted_api_origin_or_host_cannot_read_session_or_documents(boundary_api, headers):
    service, client = boundary_api
    for path in ("/api/v2/session", "/api/v2/documents"):
        assert client.get(path, headers=headers).status_code == 403
    assert service.store.documents() == []


def test_missing_or_wrong_token_cannot_queue_backup(boundary_api):
    service, client = boundary_api
    for headers in ({}, {"X-Review-Token": "not-the-session-token"}):
        assert client.post("/api/v2/backups", headers=headers).status_code == 403
    assert list((service.store.root / "backups").iterdir()) == []


@pytest.mark.parametrize("failure", ["review_error", "unexpected_error"])
def test_failed_export_removes_only_its_unregistered_partial_artifacts(boundary_api, monkeypatch, failure):
    service, client = boundary_api
    identifier = str(uuid4())
    service.store.create_document(identifier, "fixture.pdf", "fixture", {
        "status": "ready", "page_count": 1, "pages": [{"number": 1, "width": 600, "height": 800}]
    })
    sentinel = service.store.root / "artifacts" / "existing-review.pdf"
    sentinel.write_bytes(b"Unrelated existing artifact")

    def failed_export(kind, payload):
        assert kind == "export"
        Path(payload["output_path"]).write_bytes(b"incomplete export")
        Path(payload["output_path"]).with_suffix(".combined.pdf").write_bytes(b"incomplete companion")
        if failure == "review_error":
            raise ReviewError("worker_timeout", "Injected timeout")
        raise RuntimeError("Injected unexpected child failure")

    monkeypatch.setattr(service.worker, "run", failed_export)
    response = client.post(f"/api/v2/documents/{identifier}/exports", json={"revision": 1, "format": "pdf"},
                           headers={"X-Review-Token": service.token})
    assert response.status_code == (422 if failure == "review_error" else 500)
    if failure == "review_error":
        assert response.json()["code"] == "worker_timeout"
    assert list((service.store.root / "artifacts").iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"Unrelated existing artifact"
    with service.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM exports").fetchone()[0] == 0


def test_successful_export_keeps_its_registered_artifact(boundary_api, monkeypatch):
    service, client = boundary_api
    identifier = str(uuid4())
    service.store.create_document(identifier, "fixture.pdf", "fixture", {
        "status": "ready", "page_count": 1, "pages": [{"number": 1, "width": 600, "height": 800}]
    })

    def successful_export(kind, payload):
        path = Path(payload["output_path"])
        path.write_bytes(b"Saved draft text")
        return {"path": str(path), "page_count": 0, "text_verified": True}

    monkeypatch.setattr(service.worker, "run", successful_export)
    response = client.post(f"/api/v2/documents/{identifier}/exports", json={"revision": 1, "format": "txt"},
                           headers={"X-Review-Token": service.token})
    assert response.status_code == 201, response.text
    exported = response.json()
    assert exported["status"] == "draft_partial"
    artifact = service.export_record(exported["id"])["body"]
    assert Path(artifact["path"]).read_bytes() == b"Saved draft text"
    assert artifact["sha256"] == review_backup.digest(Path(artifact["path"]))
    assert client.get(f"/api/v2/exports/{exported['id']}/download").content == b"Saved draft text"
