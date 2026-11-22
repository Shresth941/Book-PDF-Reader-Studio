"""Saved export approvals cannot bypass newer publication verification rules."""
from pathlib import Path
from uuid import uuid4
import hashlib
import shutil

from fastapi import FastAPI
from fastapi.testclient import TestClient
import fitz
import pytest

from app.api.review_routes import ReviewAPI
from app.core.config import Settings
from app.infrastructure.review_store import encode, now
from app.infrastructure.structured_pdf import extract_page, inspect_pdf
from app.services.review_export import PDF_VERIFICATION_VERSION
from test_review_workflow import approve_all, source_pdf


@pytest.fixture
def current_export(tmp_path, source_pdf):
    service = ReviewAPI(Settings(data_dir=tmp_path / "version-api", minimum_free_memory_mb=0))
    app = FastAPI()
    service.install(app)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {"X-Review-Token": client.get("/api/v2/session").json()["token"]}
        identifier = str(uuid4())
        original = service.store.root / "originals" / f"{identifier}.pdf"
        shutil.copyfile(source_pdf, original)
        service.store.create_document(identifier, "source.pdf", hashlib.sha256(original.read_bytes()).hexdigest(),
                                      inspect_pdf(str(original)))
        extracted = extract_page(str(service.original(identifier)), 1)
        with service.store.connection(True) as db:
            service.store._save_page(db, identifier, {**extracted, "revision": 1}, "test-extraction", {})
        approve_all(service.store, identifier)
        revision = service.store.document(identifier)["revision"]
        snapshot = service.store.snapshot(identifier, revision)
        assert snapshot["blockers"] == []
        # The rendering suites qualify PDF content. This small seeded artifact
        # isolates saved-export version handling without starting heavy workers.
        export_id = str(uuid4())
        path = service.store.root / "artifacts" / f"{export_id}.pdf"
        with fitz.open() as pdf:
            pdf.new_page().insert_text((50, 60), "Version-gate artifact fixture.")
            pdf.save(path)
        body = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "page_count": 1, "blockers": [], "snapshot": snapshot, "layout_approvals": {},
                "verification_version": PDF_VERIFICATION_VERSION, "content_map": [],
                "render_verified": True, "text_verified": True}
        with service.store.connection(True) as db:
            db.execute("INSERT INTO exports VALUES(?,?,?,?,?,?,?)",
                       (export_id, identifier, revision, "pdf", "readable", encode(body), now()))
        yield client, service, headers, service.public_export(service.export_record(export_id))


def approve_export_pages(client, headers, artifact):
    response = None
    for number in range(1, artifact["page_count"] + 1):
        response = client.post(f"/api/v2/exports/{artifact['id']}/approvals", headers=headers,
                               json={"page": number, "reviewer": "Version regression reviewer", "confirmed": True})
        assert response.status_code == 200, response.text
    assert response is not None
    return response.json()


@pytest.mark.parametrize("stored_version", [None, PDF_VERIFICATION_VERSION - 1], ids=["legacy-missing", "outdated"])
def test_old_verification_requires_regeneration_even_with_saved_layout_approvals(current_export, stored_version):
    client, service, headers, artifact = current_export
    endpoint = f"/api/v2/exports/{artifact['id']}"
    assert approve_export_pages(client, headers, artifact)["status"] == "reviewed_complete"
    record = service.export_record(artifact["id"])
    body = record["body"]
    original_bytes = Path(body["path"]).read_bytes()
    saved_approvals = dict(body["layout_approvals"])
    if stored_version is None:
        body.pop("verification_version")
    else:
        body["verification_version"] = stored_version
    with service.store.connection(True) as db:
        db.execute("UPDATE exports SET body=? WHERE id=?", (encode(body), artifact["id"]))

    info = client.get(endpoint).json()
    assert info["current"], "The content revision is unchanged; only export verification is outdated."
    assert info["status"] == "draft_partial"
    assert info["layout_approvals"] == saved_approvals
    assert any("Create a new export" in issue for issue in info["blockers"])
    listing = client.get(f"/api/v2/documents/{artifact['document_id']}/exports").json()["exports"]
    assert next(item for item in listing if item["id"] == artifact["id"]) == info
    report = client.get(endpoint + "/report").json()
    assert report["status"] == info["status"] and report["blockers"] == info["blockers"]
    assert report["validation"]["verification_version"] == stored_version

    review_copy = client.get(endpoint + "/download")
    assert review_copy.status_code == 200
    assert review_copy.content == original_bytes
    assert "review-copy.pdf" in review_copy.headers["content-disposition"]
    approval = client.post(endpoint + "/approvals", headers=headers,
                           json={"page": 1, "reviewer": "New reviewer", "confirmed": True})
    assert approval.status_code == 409
    assert approval.json()["code"] == "export_verification_outdated"
    final = client.get(endpoint + "/download?final=true")
    assert final.status_code == 409
    assert final.json()["code"] == "final_blocked"
    assert service.export_record(artifact["id"])["body"] == body, "Reads and rejected approvals must not upgrade old exports."


def test_current_verification_still_requires_and_accepts_every_page_approval(current_export):
    client, service, headers, artifact = current_export
    endpoint = f"/api/v2/exports/{artifact['id']}"
    assert artifact["status"] == "layout_review_required"
    assert artifact["blockers"] == []
    assert client.get(endpoint + "/report").json()["validation"]["verification_version"] == PDF_VERIFICATION_VERSION
    assert client.get(endpoint + "/download?final=true").status_code == 409

    approved = approve_export_pages(client, headers, artifact)
    assert approved["status"] == "reviewed_complete" and approved["blockers"] == []
    final = client.get(endpoint + "/download?final=true")
    assert final.status_code == 200
    assert final.content == Path(service.export_record(artifact["id"])["body"]["path"]).read_bytes()
    assert "reviewed-complete.pdf" in final.headers["content-disposition"]


@pytest.mark.parametrize("form_inventory", [
    {"widgets": [], "limitations": ["The widget inventory could not be read completely."]},
    {"widgets": [{"xref": 99, "field_name": "Previously missed applicant"}], "limitations": []},
], ids=["legacy-incomplete-inventory", "missing-field-mapping"])
def test_v3_form_exports_cannot_reuse_cached_complete_status_after_upgrade(current_export, form_inventory):
    client, service, headers, artifact = current_export
    endpoint = f"/api/v2/exports/{artifact['id']}"
    approve_export_pages(client, headers, artifact)
    record = service.export_record(artifact["id"])
    body = record["body"]
    original_bytes = Path(body["path"]).read_bytes()
    # Reproduce a saved pre-upgrade page/export: version 3 did not include the
    # inventory/mapping rule, so its cached blockers and approvals looked final.
    page = body["snapshot"]["pages"][0]
    page["form_evidence"] = form_inventory
    body["verification_version"] = 3
    assert body["blockers"] == [] and body["layout_approvals"]
    with service.store.connection(True) as db:
        db.execute("UPDATE pages SET body=? WHERE document_id=? AND number=?",
                   (encode(page), record["document_id"], page["number"]))
        db.execute("UPDATE exports SET body=? WHERE id=?", (encode(body), artifact["id"]))
    assert service.store.snapshot(record["document_id"], record["revision"])["blockers"]

    info = client.get(endpoint).json()
    assert info["current"], "The application upgrade alone must enforce the new rule."
    assert info["status"] == "draft_partial"
    assert any("Create a new export" in issue for issue in info["blockers"])
    approval = client.post(endpoint + "/approvals", headers=headers,
                           json={"page": 1, "reviewer": "Upgrade reviewer", "confirmed": True})
    assert approval.status_code == 409
    assert approval.json()["code"] == "export_verification_outdated"
    final = client.get(endpoint + "/download?final=true")
    assert final.status_code == 409 and final.json()["code"] == "final_blocked"
    assert client.get(endpoint + "/download").content == original_bytes
    assert service.export_record(artifact["id"])["body"] == body
