"""Form-evidence integration through extraction, review gates and local APIs."""
from copy import deepcopy
import hashlib
from uuid import uuid4

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.review_routes import ReviewAPI
from app.core.config import Settings
from app.core.review import ReviewError, region_blockers
from app.infrastructure.review_store import ReviewStore
from app.infrastructure.structured_pdf import extract_page, inspect_pdf
from app.services.review_export import build_export


@pytest.fixture
def stale_form(tmp_path):
    path = tmp_path / "stale-form.pdf"
    with fitz.open() as document:
        page = document.new_page()
        widget = fitz.Widget()
        widget.field_name = "applicant"
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.field_value = "Visible original"
        widget.text_fontsize = 12
        widget.rect = fitz.Rect(30, 40, 260, 80)
        page.add_widget(widget)
        data = document.tobytes()
    with fitz.open(stream=data, filetype="pdf") as document:
        page = document[0]
        xref = next(page.widgets()).xref
        # Simulate a stored value changed without updating the appearance.
        document.xref_set_key(xref, "V", fitz.get_pdf_str("Stored newer value"))
        document.save(path)
    return path


@pytest.fixture
def form_workspace(tmp_path, stale_form):
    store = ReviewStore(tmp_path / "workspace")
    identifier = str(uuid4())
    original = store.root / "originals" / f"{identifier}.pdf"
    original.write_bytes(stale_form.read_bytes())
    store.create_document(identifier, stale_form.name, hashlib.sha256(original.read_bytes()).hexdigest(), inspect_pdf(original))
    extracted = extract_page(original, 1)
    with store.connection(True) as db:
        store._save_page(db, identifier, {**extracted, "revision": 1}, "fixture_extract", {})
    return store, identifier


def form_region(page):
    return next(region for region in page["regions"] if region["method"] == "form-field")


def edit_form(store, identifier, changes):
    page = store.page(identifier, 1)
    return store.mutate_region(identifier, 1, page["revision"], form_region(page)["id"], changes, "Reviewer")


def prepared_form(store, identifier, note=""):
    return edit_form(store, identifier, {
        "source": "Visible original", "translation": "दिखाई देने वाला मूल पाठ",
        "source_language": "en", "disposition": "translate", "form_review_note": note,
    })


def test_stale_appearance_reaches_unresolved_region_with_raw_evidence(stale_form):
    original = stale_form.read_bytes()
    page = extract_page(stale_form, 1)
    candidate = form_region(page)
    evidence = candidate["form_evidence"]
    assert candidate["raw"] == "Stored newer value"
    assert candidate["disposition"] == "unreadable"
    assert not candidate["source_approved"] and not candidate["translation_approved"]
    assert candidate["form_review_note"] == ""
    assert evidence["stored_value"] == "Stored newer value"
    assert evidence["appearance"]["text"].strip() == "Visible original"
    assert evidence["status"] == "contradiction"
    assert evidence in page["form_evidence"]["widgets"]
    assert evidence["provenance"]["evidence_sha256"]
    assert any("differs" in note for note in candidate["evidence"])
    assert stale_form.read_bytes() == original


@pytest.mark.parametrize("note", ["", "Checked", " " * 20])
def test_form_source_approval_requires_a_meaningful_reconciliation_note(form_workspace, note):
    store, identifier = form_workspace
    page = prepared_form(store, identifier, note)
    candidate = form_region(page)
    with pytest.raises(ReviewError) as error:
        store.approve(identifier, 1, page["revision"], "source", "Reviewer", candidate["id"])
    assert error.value.code == "form_reconciliation_required"
    assert not form_region(store.page(identifier, 1))["source_approved"]
    assert any("stored form value" in issue for issue in region_blockers(candidate, [], "hi"))


def test_form_reconciliation_allows_explicit_review_without_changing_evidence(form_workspace):
    store, identifier = form_workspace
    before = deepcopy(form_region(store.page(identifier, 1))["form_evidence"])
    page = prepared_form(store, identifier, "Compared the original: use its visible text; the stored value is stale.")
    candidate = form_region(page)
    page = store.approve(identifier, 1, page["revision"], "source", "Reviewer", candidate["id"])
    page = store.approve(identifier, 1, page["revision"], "translation", "Reviewer", candidate["id"])
    approved = form_region(page)
    assert approved["source_approved"] and approved["translation_approved"]
    assert approved["form_evidence"] == before
    assert approved["raw"] == "Stored newer value"
    assert approved["source"] == "Visible original"
    assert not region_blockers(approved, [], "hi")


@pytest.mark.parametrize("missing", ["null_hash", "empty_evidence", "absent_evidence"])
def test_incomplete_evidence_hash_cannot_be_approved_even_with_note(form_workspace, missing):
    store, identifier = form_workspace
    page = prepared_form(store, identifier, "Checked the original visually and selected its text.")
    candidate = form_region(page)
    if missing == "null_hash":
        candidate["form_evidence"]["provenance"]["evidence_sha256"] = None
    elif missing == "empty_evidence":
        candidate["form_evidence"] = {}
    else:
        candidate.pop("form_evidence")
    with store.connection(True) as db:
        store._save_page(db, identifier, page, "fixture_incomplete_evidence", {})
    current = store.page(identifier, 1)
    with pytest.raises(ReviewError) as error:
        store.approve(identifier, 1, current["revision"], "source", "Reviewer", candidate["id"])
    assert error.value.code == "form_evidence_incomplete"
    assert not form_region(store.page(identifier, 1))["source_approved"]
    assert any("inspected completely" in issue for issue in current["blockers"])


@pytest.mark.parametrize("change", [
    {"form_review_note": "A different reconciliation decision needs review again."},
    {"source": "A newly corrected visible value"},
])
def test_form_note_or_source_change_invalidates_prior_approvals(form_workspace, change):
    store, identifier = form_workspace
    page = prepared_form(store, identifier, "Use the visible value after comparing the stored and displayed field.")
    candidate = form_region(page)
    page = store.approve(identifier, 1, page["revision"], "source", "Reviewer", candidate["id"])
    store.approve(identifier, 1, page["revision"], "translation", "Reviewer", candidate["id"])
    updated = edit_form(store, identifier, change)
    assert not form_region(updated)["source_approved"]
    assert not form_region(updated)["translation_approved"]
    assert not updated["coverage_approved"]
    assert form_region(updated)["raw"] == "Stored newer value"


@pytest.fixture
def xfa_pdf(tmp_path, stale_form):
    path = tmp_path / "dynamic.pdf"
    with fitz.open(stale_form) as document:
        packet = document.get_new_xref()
        document.update_object(packet, "<< >>")
        document.update_stream(packet, b"<xfa>synthetic dynamic form fixture</xfa>")
        document.xref_set_key(document.pdf_catalog(), "AcroForm/XFA", f"{packet} 0 R")
        document.save(path)
    return path


@pytest.mark.parametrize("operation", [inspect_pdf, lambda path: extract_page(path, 1)])
def test_xfa_preflight_and_extraction_fail_explicitly_without_changing_original(xfa_pdf, operation):
    original = xfa_pdf.read_bytes()
    with pytest.raises(ReviewError) as error:
        operation(xfa_pdf)
    assert error.value.code == "xfa_unsupported"
    assert xfa_pdf.read_bytes() == original


@pytest.mark.parametrize("format,mode", [("pdf", "readable"), ("pdf", "faithful"), ("pdf", "companion"), ("docx", "readable"), ("txt", "readable")])
def test_xfa_export_is_blocked_before_output_processing(xfa_pdf, tmp_path, monkeypatch, format, mode):
    from app.services import review_export
    original = xfa_pdf.read_bytes()
    output = tmp_path / f"blocked.{format}"
    snapshot = {"document": {"filename": xfa_pdf.name, "revision": 1}, "pages": [], "blockers": []}
    def must_not_render(*args, **kwargs):
        pytest.fail("XFA must be rejected before running any PDF renderer")
    monkeypatch.setattr(review_export, "readable_pdf", must_not_render)
    monkeypatch.setattr(review_export, "faithful_pdf", must_not_render)
    with pytest.raises(ReviewError) as error:
        build_export(snapshot, xfa_pdf, output, format, mode)
    assert error.value.code == "xfa_unsupported"
    assert not output.exists()
    assert xfa_pdf.read_bytes() == original


def test_api_accepts_form_note_but_cannot_replace_evidence(form_workspace):
    store, identifier = form_workspace
    service = ReviewAPI(Settings(_env_file=None, data_dir=store.root, runtime_mode="local"))
    app = FastAPI()
    service.install(app)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {"X-Review-Token": client.get("/api/v2/session").json()["token"]}
        page = service.store.page(identifier, 1)
        candidate = form_region(page)
        url = f"/api/v2/documents/{identifier}/pages/1/regions/{candidate['id']}"
        edit = {"revision": page["revision"], "reviewer": "Reviewer", "source": "Visible original",
                "translation": "दिखाई देने वाला मूल पाठ", "source_language": "en", "disposition": "translate",
                "contains_text": True, "kind": "manual", "form_review_note": "Visible text is authoritative; the stored value is stale."}
        response = client.patch(url, json=edit, headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        assert form_region(result)["form_review_note"] == edit["form_review_note"]
        assert form_region(result)["form_evidence"] == candidate["form_evidence"]
        approval = client.post(f"/api/v2/documents/{identifier}/pages/1/approvals", headers=headers,
                               json={"revision": result["revision"], "kind": "source", "region_id": candidate["id"]})
        assert approval.status_code == 200, approval.text
        forged = {**edit, "revision": approval.json()["revision"], "form_evidence": {"status": "matching"}}
        assert client.patch(url, json=forged, headers=headers).status_code == 422
        assert service.store.page(identifier, 1)["revision"] == approval.json()["revision"]


def test_api_xfa_import_retains_original_and_actionable_status(tmp_path, xfa_pdf, monkeypatch):
    service = ReviewAPI(Settings(_env_file=None, data_dir=tmp_path / "xfa-workspace", runtime_mode="local"))
    def inspection_only(operation, payload, *args):
        assert operation == "inspect"
        return inspect_pdf(payload["path"])
    monkeypatch.setattr(service.worker, "run", inspection_only)
    app = FastAPI()
    service.install(app)
    original = xfa_pdf.read_bytes()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {"X-Review-Token": client.get("/api/v2/session").json()["token"]}
        response = client.post("/api/v2/documents", headers=headers, files={"file": (xfa_pdf.name, original, "application/pdf")})
        assert response.status_code == 201, response.text
        doc = response.json()
        assert doc["status"] == "needs_attention"
        assert doc["metadata"]["code"] == "xfa_unsupported"
        assert doc["sha256"] == hashlib.sha256(original).hexdigest()
        assert client.get(f"/api/v2/documents/{doc['id']}/original").content == original
        assert xfa_pdf.read_bytes() == original
