import asyncio
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.review_routes import ReviewAPI
from app.core.config import Settings
from app.core.review import ReviewError, page_blockers, translation_issues
from app.infrastructure.bounded_worker import BoundedWorker
from app.infrastructure.review_store import ReviewStore, encode
from app.infrastructure.structured_pdf import inspect_pdf, extract_page, region
from app.services.document_service import DocumentService
from app.services.language_service import _document_domain
from app.services.local_translation import split_exact
from app.services.review_backup import create_backup, restore_backup
from app.services.review_export import build_export


@pytest.fixture
def source_pdf(tmp_path):
    path = tmp_path / "sample.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "The debtor owes 100 rupees.")
        page.insert_text((72, 112), "Do not omit this separate clause.")
        pdf.save(path)
    return path


@pytest.fixture
def workspace(tmp_path, source_pdf):
    import shutil
    from app.services.review_backup import digest
    store = ReviewStore(tmp_path / "workspace")
    identifier = str(uuid4())
    original = store.root / "originals" / f"{identifier}.pdf"
    shutil.copyfile(source_pdf, original)
    store.create_document(identifier, "sample.pdf", digest(original), inspect_pdf(str(original)))
    page = extract_page(str(original), 1)
    with store.connection(True) as db:
        store._save_page(db, identifier, {**page, "revision": 1}, "test_extract", {})
    return store, identifier


def approve_all(store, identifier):
    p = store.page(identifier, 1)
    for original in list(p["regions"]):
        p = store.mutate_region(identifier, 1, p["revision"], original["id"],
            {"source_language": "en", "translation": "ऋणी को 100 रुपये देने हैं।" if "100" in original["source"] else "इस अलग खंड को न छोड़ें।"}, "tester")
    for original in list(p["regions"]):
        p = store.approve(identifier, 1, p["revision"], "source", "tester", original["id"])
        r = next(r for r in p["regions"] if r["id"] == original["id"])
        p = store.approve(identifier, 1, p["revision"], "translation", "tester", original["id"], {i["code"]: "Verified against the entire source clause." for i in r["checks"]})
    return store.approve(identifier, 1, p["revision"], "coverage", "tester")


@pytest.mark.parametrize("count", [512, 513, 740, 1000])
def test_cache_eviction_never_changes_content(count):
    class Storage:
        def path_for(self, identifier): return Path("unused.pdf")
    class Reader:
        def extract(self, path, start, end): return [{"page": n, "text": f"Page {n}"} for n in range(start, end + 1)]
    service = DocumentService(Storage(), Reader())
    identifier = uuid4()
    for start, end in [(1, count), (count, 1), (1, min(count, 5))]:
        assert service.pages(identifier, start, end) == [{"page": n, "text": f"Page {n}"} for n in range(min(start, end), max(start, end) + 1)]


def test_missing_page_is_an_error_not_blank():
    class Storage:
        def path_for(self, identifier): return Path("unused.pdf")
    class Reader:
        def extract(self, *args): return [{"page": 1, "text": "one"}]
    with pytest.raises(ValueError, match="every requested page"):
        DocumentService(Storage(), Reader()).pages(uuid4(), 1, 2)


def test_numbering_and_uncertain_ocr_are_preserved():
    from app.infrastructure.pdf_reader import PyMuPdfReader
    lines = ["1. One", "2. Two", "3. Three", "14. Fourteen"]
    assert PyMuPdfReader._repair_contents_numbering(lines) == lines
    assert PyMuPdfReader._clean_ocr_lines(["y", "uncer-", "tain_\ufffd"]) == "y\nuncer-\ntain_\ufffd"
    assert _document_domain("Understanding fractions") == "general"


def test_script_does_not_claim_language():
    from app.core.review import language_hint
    assert language_hint("bonjour") == "latin"
    assert language_hint("मराठी") == "devanagari"
    assert language_hint("नमस्ते hello") == "mixed"


def test_semantic_checks_catch_roles_even_when_numbers_match():
    issues = translation_issues("Alice owes Bob 100 rupees unless the debt is waived.", "Bob owes Alice 100 rupees.")
    codes = {i["code"] for i in issues}
    assert "numbers_changed" not in codes
    assert {"fact_relationships", "legal_scope"} <= codes
    assert "numbers_changed" in {i["code"] for i in translation_issues("100 and 100", "100")}
    assert "numbers_changed" not in {i["code"] for i in translation_issues("१००", "100")}


def test_revision_conflict_preserves_user_edit(workspace):
    store, identifier = workspace
    page = store.page(identifier, 1)
    r = page["regions"][0]
    changed = store.mutate_region(identifier, 1, page["revision"], r["id"], {"source": "User correction"}, "tester")
    with pytest.raises(ReviewError, match="changed"):
        store.mutate_region(identifier, 1, page["revision"], r["id"], {"source": "Stale response"}, "worker")
    assert store.page(identifier, 1)["regions"][0]["source"] == "User correction"
    assert ReviewStore(store.root).page(identifier, 1)["revision"] == changed["revision"]


def test_coverage_is_separate_and_new_region_invalidates_it(workspace):
    store, identifier = workspace
    page = approve_all(store, identifier)
    assert not page["blockers"]
    page = store.add_region(identifier, 1, page["revision"], {"bbox": [20, 200, 150, 250]}, "tester")
    assert not page["coverage_approved"]
    assert any("Unresolved" in b for b in page["blockers"])


def test_approval_requires_specific_meaning_notes(workspace):
    store, identifier = workspace
    page = store.page(identifier, 1); r = page["regions"][0]
    page = store.mutate_region(identifier, 1, page["revision"], r["id"], {"source_language": "en", "translation": "ऋणी 100 रुपये देता है।"}, "tester")
    with pytest.raises(ReviewError, match="source"):
        store.approve(identifier, 1, page["revision"], "translation", "tester", r["id"])
    page = store.approve(identifier, 1, page["revision"], "source", "tester", r["id"])
    with pytest.raises(ReviewError, match="who owes"):
        store.approve(identifier, 1, page["revision"], "translation", "tester", r["id"], {})
    assert not store.page(identifier, 1)["regions"][0]["translation_approved"]


def test_glossary_invalidates_prior_approvals(workspace):
    store, identifier = workspace
    approve_all(store, identifier)
    store.add_glossary(identifier, store.document(identifier)["revision"], {"source": "debtor", "target": "ऋणी", "sense": "debtor in this document", "reference": "page 1"})
    assert not store.page(identifier, 1)["regions"][0]["translation_approved"]
    assert not store.page(identifier, 1)["coverage_approved"]


def test_cancelled_worker_cannot_publish(workspace):
    store, identifier = workspace
    job = store.enqueue(identifier, "translate", {"pages": [1]})
    with store.connection(True) as db:
        db.execute("UPDATE jobs SET status='running',attempt=1 WHERE id=?", (job["id"],))
    page = store.page(identifier, 1)
    store.control(job["id"], "cancel")
    with pytest.raises(ReviewError, match="superseded"):
        store.checkpoint(job["id"], 1, 1, page["revision"], page)
    assert store.page(identifier, 1)["revision"] == page["revision"]


def test_restart_pauses_interrupted_jobs(workspace):
    store, identifier = workspace
    job = store.enqueue(identifier, "extract", {"pages": [1]})
    store.recover()
    assert store.job(job["id"])["status"] == "paused"


def test_real_job_consumer_checkpoints_extraction(tmp_path, source_pdf):
    import shutil
    from app.services.review_jobs import JobRunner
    from app.services.review_backup import digest
    store = ReviewStore(tmp_path / "job-store")
    identifier = str(uuid4())
    original = store.root / "originals" / f"{identifier}.pdf"
    shutil.copyfile(source_pdf, original)
    store.create_document(identifier, "sample.pdf", digest(original), inspect_pdf(str(original)))
    settings = Settings(data_dir=store.root, minimum_free_memory_mb=0)
    runner = JobRunner(store, BoundedWorker(settings), settings)
    job = store.enqueue(identifier, "extract", {"pages": [1]})
    with store.connection(True) as db:
        db.execute("UPDATE jobs SET status='running',attempt=1 WHERE id=?", (job["id"],))
    runner.run(store.job(job["id"]))
    assert store.job(job["id"])["status"] == "completed", store.job(job["id"])
    assert len(store.page(identifier, 1)["regions"]) == 2


def test_chunking_retains_every_character():
    text = "A long clause. यह एक लंबा खंड है। " * 20
    pieces = split_exact(text, lambda s: len(s) <= 45)
    assert "".join(p[2] for p in pieces) == text
    assert all(end - start <= 45 and text[start:end] == value for start, end, value in pieces)
    assert all(a[1] == b[0] for a, b in zip(pieces, pieces[1:]))


def test_backup_restore_preserves_originals_and_reviews(workspace, tmp_path):
    store, identifier = workspace
    approve_all(store, identifier)
    backup = create_backup(store)
    restored = restore_backup(backup, tmp_path / "restored")
    assert ReviewStore(restored).document(identifier)["progress"]["content_approved"] == 1
    with pytest.raises(ReviewError, match="new"):
        restore_backup(backup, restored)


def test_isolated_preflight_and_render(source_pdf, tmp_path):
    settings = Settings(data_dir=tmp_path, minimum_free_memory_mb=0, worker_timeout_seconds=30)
    worker = BoundedWorker(settings)
    result = worker.run("inspect", {"path": str(source_pdf)})
    assert result["page_count"] == 1
    result = worker.run("render", {"path": str(source_pdf), "number": 1, "output_path": str(tmp_path / "preview.png")})
    assert Path(result["path"]).is_file()


def test_password_document_requires_password(tmp_path):
    path = tmp_path / "encrypted.pdf"
    with fitz.open() as pdf:
        pdf.new_page()
        pdf.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="reader")
    assert inspect_pdf(str(path))["status"] == "password_required"
    assert inspect_pdf(str(path), password="reader")["status"] == "ready"


def test_review_export_renders_hindi_and_keeps_original(workspace, tmp_path):
    store, identifier = workspace
    from app.services.review_backup import digest
    original = store.root / "originals" / f"{identifier}.pdf"
    before = digest(original)
    approve_all(store, identifier)
    snapshot = store.snapshot(identifier, store.document(identifier)["revision"])
    target = tmp_path / "translated.pdf"
    result = build_export(snapshot, str(original), str(target), "pdf", "readable")
    assert result["page_count"] >= 1
    with fitz.open(target) as pdf:
        text = "".join(p.get_text() for p in pdf)
        assert "100" in text and "ऋणी" in text
        pdf[0].get_pixmap().save(tmp_path / "translated.png")
    assert digest(original) == before


def test_api_boundaries_and_final_gate(tmp_path, source_pdf):
    settings = Settings(data_dir=tmp_path / "api", minimum_free_memory_mb=0)
    api = ReviewAPI(settings)
    app = FastAPI(); api.install(app)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/v2/documents").status_code == 403
        assert client.get("/api/v2/session", headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert client.post("/api/translate", json={"text": "private"}).status_code == 410
        headers = {"X-Review-Token": client.get("/api/v2/session").json()["token"]}
        response = client.post("/api/v2/documents", files={"file": ("source.pdf", source_pdf.read_bytes(), "application/pdf")}, headers=headers)
        assert response.status_code == 201, response.text
        doc = response.json()
        assert doc["status"] == "ready", doc
        assert client.get(f"/api/v2/documents/{doc['id']}/pages/1").json()["status"] == "pending"
        response = client.post(f"/api/v2/documents/{doc['id']}/exports", json={"revision": doc["revision"], "format": "pdf", "mode": "readable"}, headers=headers)
        assert response.status_code == 201, response.text
        exported = response.json()
        assert exported["status"] == "draft_partial"
        assert client.get(f"/api/v2/exports/{exported['id']}/download?final=true").status_code == 409
        assert client.post(f"/api/v2/exports/{exported['id']}/approvals", json={"page": 1, "reviewer": "tester", "confirmed": True}, headers=headers).status_code == 422


@pytest.mark.parametrize("reader_compatibility", [False, True])
def test_final_download_requires_current_layout_and_unchanged_bytes(tmp_path, source_pdf, reader_compatibility):
    settings = Settings(data_dir=tmp_path / "final-api", minimum_free_memory_mb=0)
    service = ReviewAPI(settings)
    app = FastAPI(); service.install(app)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {"X-Review-Token": client.get("/api/v2/session").json()["token"]}
        response = client.post("/api/v2/documents", files={"file": ("source.pdf", source_pdf.read_bytes(), "application/pdf")}, headers=headers)
        doc = response.json(); identifier = doc["id"]
        extracted = extract_page(str(service.original(identifier)), 1)
        with service.store.connection(True) as db:
            service.store._save_page(db, identifier, {**extracted, "revision": 1}, "test-extraction", {})
        approve_all(service.store, identifier)
        if reader_compatibility:
            page = service.store.page(identifier, 1)
            region_id = page["regions"][0]["id"]
            page = service.store.mutate_region(identifier, 1, page["revision"], region_id,
                {"translation": page["regions"][0]["translation"] + " सत्यापित"}, "tester")
            page = service.store.approve(identifier, 1, page["revision"], "source", "tester", region_id)
            page = service.store.approve(identifier, 1, page["revision"], "translation", "tester", region_id,
                {i["code"]: "Verified source meaning and all protected facts." for i in page["regions"][0]["checks"]})
            service.store.approve(identifier, 1, page["revision"], "coverage", "tester")
        response = client.post(f"/api/v2/documents/{identifier}/exports", json={"revision": service.store.document(identifier)["revision"], "format": "pdf"}, headers=headers)
        assert response.status_code == 201, response.text
        artifact = response.json()
        assert client.get(f"/api/v2/exports/{artifact['id']}/download?final=true").status_code == 409
        if reader_compatibility:
            assert artifact["blockers"] == []
            assert service.export_record(artifact["id"])["body"]["text_encoding"] == "verified-vector-and-logical-text-v1"
        for number in range(1, artifact["page_count"] + 1):
            approval = client.post(f"/api/v2/exports/{artifact['id']}/approvals", json={"page": number, "reviewer": "tester", "confirmed": True}, headers=headers)
            assert approval.status_code == 200, approval.text
        assert client.get(f"/api/v2/exports/{artifact['id']}/download?final=true").status_code == 200
        record = service.export_record(artifact["id"])
        path = Path(record["body"]["path"])
        original = path.read_bytes()
        path.write_bytes(original + b"tampered")
        assert client.get(f"/api/v2/exports/{artifact['id']}/download?final=true").status_code == 409
        path.write_bytes(original)
        page = service.store.page(identifier, 1)
        service.store.mutate_region(identifier, 1, page["revision"], page["regions"][0]["id"], {"translation": "Edited after review"}, "tester")
        assert client.get(f"/api/v2/exports/{artifact['id']}/download?final=true").status_code == 409
