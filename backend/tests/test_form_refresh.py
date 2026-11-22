"""Evidence refresh cannot replace reviewed text or guess a field's identity."""
from copy import deepcopy
from uuid import uuid4

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.review_routes import ReviewAPI
from app.core.config import Settings
from app.core.form_refresh import mapping_suggestions, merge_form_evidence, form_inventory_blockers
from app.core.review import ReviewError
from app.infrastructure.structured_pdf import inspect_page_forms, verify_original
from app.infrastructure import form_evidence
from test_form_review import form_workspace, stale_form, form_region, prepared_form


def inspect(store, identifier):
    return inspect_page_forms(store.root / "originals" / f"{identifier}.pdf", 1,
                              store.document(identifier)["sha256"])


def preview(store, identifier, inspection=None):
    return store.prepare_form_refresh(identifier, 1, store.page(identifier, 1)["revision"],
        store.document(identifier)["revision"], inspection or inspect(store, identifier))


def apply(store, identifier, value, bindings=None):
    if bindings is None:
        bindings = [{"widget_xref": r["form_evidence"]["xref"],
                     "region_id": value["suggestions"].get(str(r["form_evidence"]["xref"]))}
                    for r in value["inspection"]["regions"]]
    return store.apply_form_refresh(identifier, 1, value["page_revision"], value["document_revision"],
        value["id"], bindings, "Compared each original field and its saved region.", "Reviewer",
        store.document(identifier)["sha256"])


@pytest.mark.parametrize("legacy", ["missing", "empty", "no_hash"])
def test_legacy_refresh_preserves_every_edit_and_clears_approvals(form_workspace, legacy):
    store, identifier = form_workspace
    page = prepared_form(store, identifier, "Use visible text after checking the original.")
    region = form_region(page)
    if legacy == "missing": region.pop("form_evidence")
    elif legacy == "empty": region["form_evidence"] = {}
    else: region["form_evidence"]["provenance"]["evidence_sha256"] = None
    region["source_approved"] = region["translation_approved"] = page["coverage_approved"] = True
    with store.connection(True) as db: store._save_page(db, identifier, page, "fixture_legacy", {})
    before = deepcopy(form_region(store.page(identifier, 1)))
    proposal = preview(store, identifier)
    assert form_region(store.page(identifier, 1)) == before, "Preview must not mutate content."
    after_page = apply(store, identifier, proposal)
    after = form_region(after_page)
    for key in ("id", "raw", "source", "translation", "form_review_note", "order", "bbox", "disposition", "source_language", "reason", "alternatives", "evidence"):
        assert after[key] == before[key]
    assert after["form_evidence"]["provenance"]["evidence_sha256"]
    assert after["form_evidence_changed"]
    assert not after["source_approved"] and not after["translation_approved"] and not after_page["coverage_approved"]
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM events WHERE action='form_evidence_refresh'").fetchone()[0] == 1


def test_identical_refresh_retains_approvals_and_revisions(form_workspace):
    store, identifier = form_workspace
    page = prepared_form(store, identifier, "Checked stored value and existing appearance.")
    rid = form_region(page)["id"]
    page = store.approve(identifier, 1, page["revision"], "source", "Reviewer", rid)
    page = store.approve(identifier, 1, page["revision"], "translation", "Reviewer", rid)
    page = store.approve(identifier, 1, page["revision"], "coverage", "Reviewer")
    revision = store.document(identifier)["revision"]
    value = preview(store, identifier)
    assert apply(store, identifier, value) == page
    assert store.document(identifier)["revision"] == revision
    with pytest.raises(ReviewError, match="already applied"):
        apply(store, identifier, value)


@pytest.mark.parametrize("change", ["edit", "context", "source_approval", "replace_preview"])
def test_stale_or_replaced_previews_cannot_publish(form_workspace, change):
    store, identifier = form_workspace
    value = preview(store, identifier)
    page = store.page(identifier, 1)
    if change == "edit":
        store.mutate_region(identifier, 1, page["revision"], form_region(page)["id"], {"source": "new user edit"}, "Reviewer")
    elif change == "context":
        store.update_context(identifier, store.document(identifier)["revision"], {"target": "en", "domain": "legal", "jurisdiction": "India"})
    elif change == "source_approval":
        page = prepared_form(store, identifier, "Check against original before source approval.")
        store.approve(identifier, 1, page["revision"], "source", "Reviewer", form_region(page)["id"])
    else:
        preview(store, identifier)
    before = store.page(identifier, 1)
    with pytest.raises(ReviewError) as error: apply(store, identifier, value)
    assert error.value.status == 409
    assert store.page(identifier, 1) == before


@pytest.mark.parametrize("bad", ["page", "fingerprint", "coordinates", "incomplete", "duplicate_xref"])
def test_invalid_inspection_never_publishes_preview(form_workspace, bad):
    store, identifier = form_workspace
    evidence = inspect(store, identifier)
    if bad == "page": evidence["number"] = 2
    elif bad == "fingerprint": evidence["document_sha256"] = "0" * 64
    elif bad == "coordinates": evidence["width"] += 20
    elif bad == "incomplete": evidence["form_evidence"]["inventory_complete"] = False
    else:
        evidence["form_evidence"]["widgets"].append(deepcopy(evidence["form_evidence"]["widgets"][0]))
        evidence["regions"].append(deepcopy(evidence["regions"][0]))
    before = store.page(identifier, 1)
    with pytest.raises(ReviewError): preview(store, identifier, evidence)
    assert store.page(identifier, 1) == before
    with store.connection() as db: assert not db.execute("SELECT * FROM form_previews").fetchall()


@pytest.mark.parametrize("bad", ["missing", "new_instead_of_existing", "wrong_region", "duplicate", "wrong_widget"])
def test_invalid_mappings_are_atomic(form_workspace, bad):
    store, identifier = form_workspace
    value = preview(store, identifier)
    field = value["inspection"]["regions"][0]["form_evidence"]
    binding = {"widget_xref": field["xref"], "region_id": form_region(store.page(identifier, 1))["id"]}
    bindings = [binding]
    if bad == "missing": bindings = []
    elif bad == "new_instead_of_existing": binding["region_id"] = None
    elif bad == "wrong_region": binding["region_id"] = str(uuid4())
    elif bad == "duplicate": bindings *= 2
    else: binding["widget_xref"] += 1
    before = store.page(identifier, 1)
    with pytest.raises(ReviewError): apply(store, identifier, value, bindings)
    assert store.page(identifier, 1) == before


def test_ambiguous_legacy_overlap_requires_explicit_one_to_one_choices(form_workspace):
    store, identifier = form_workspace
    page, evidence = store.page(identifier, 1), inspect(store, identifier)
    old = form_region(page)
    old.pop("form_evidence")
    other = deepcopy(old); other["id"] = str(uuid4()); other["order"] += 1
    page["regions"].append(other)
    second = deepcopy(evidence["regions"][0]); second["id"] = str(uuid4())
    second["form_evidence"]["xref"] += 100
    second["form_evidence"]["provenance"]["widget_xref"] += 100
    evidence["regions"].append(second)
    assert all(value is None for value in mapping_suggestions(page, evidence).values())
    bindings = [{"widget_xref": r["form_evidence"]["xref"], "region_id": saved["id"]}
                for r, saved in zip(evidence["regions"], [old, other])]
    result, changes, changed = merge_form_evidence(page, evidence, bindings)
    assert changed and len(changes) == 2
    assert [r["id"] for r in result["regions"]] == [r["id"] for r in page["regions"]]


def test_known_identity_cannot_be_swapped_even_at_same_position(form_workspace):
    store, identifier = form_workspace
    page, evidence = store.page(identifier, 1), inspect(store, identifier)
    old = form_region(page)
    field = evidence["regions"][0]["form_evidence"]
    field["xref"] += 100; field["provenance"]["widget_xref"] += 100
    with pytest.raises(ReviewError, match="identity or position"):
        merge_form_evidence(page, evidence, [{"widget_xref": field["xref"], "region_id": old["id"]}])


def test_newly_identified_widget_requires_explicit_new_region(form_workspace):
    store, identifier = form_workspace
    page = store.page(identifier, 1)
    page["regions"] = [r for r in page["regions"] if r["method"] != "form-field"]
    with store.connection(True) as db: store._save_page(db, identifier, page, "fixture_missed_field", {})
    value = preview(store, identifier)
    assert list(value["suggestions"].values()) == [None]
    applied = apply(store, identifier, value)
    assert form_region(applied)["disposition"] == "unreadable"
    assert not form_region(applied)["source_approved"]


def test_changed_original_or_incomplete_inventory_cannot_refresh(form_workspace, monkeypatch):
    store, identifier = form_workspace
    path = store.root / "originals" / f"{identifier}.pdf"
    before = path.read_bytes()
    with pytest.raises(ReviewError, match="fingerprint"):
        inspect_page_forms(path, 1, "0" * 64)
    monkeypatch.setattr(form_evidence, "MAX_WIDGETS", 0)
    with pytest.raises(ReviewError, match="complete field inventory"):
        inspect(store, identifier)
    assert path.read_bytes() == before


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_worker_keeps_rotation_and_original_coordinates(tmp_path, stale_form, rotation):
    import hashlib
    path = tmp_path / "rotated.pdf"
    with fitz.open(stale_form) as pdf:
        pdf[0].set_rotation(rotation)
        pdf.save(path)
    original = path.read_bytes()
    value = inspect_page_forms(path, 1, hashlib.sha256(original).hexdigest())
    assert value["regions"][0]["bbox"] == [30, 40, 260, 80]
    assert value["width"] == 595 and value["height"] == 842
    assert original == path.read_bytes()


def test_api_preview_apply_and_original_tamper_guard(form_workspace, monkeypatch):
    store, identifier = form_workspace
    page = store.page(identifier, 1)
    form_region(page).pop("form_evidence")
    with store.connection(True) as db: store._save_page(db, identifier, page, "fixture_legacy", {})
    service = ReviewAPI(Settings(data_dir=store.root))
    app = FastAPI(); service.install(app)
    def run(operation, payload):
        return {"inspect_forms": inspect_page_forms, "verify_original": verify_original}[operation](**payload)
    monkeypatch.setattr(service.worker, "run", run)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        token = {"X-Review-Token": client.get("/api/v2/session").json()["token"]}
        base = f"/api/v2/documents/{identifier}/pages/1/form-evidence"
        request = {"revision": store.page(identifier, 1)["revision"], "document_revision": store.document(identifier)["revision"]}
        result = client.post(base + "/preview", headers=token, json=request)
        assert result.status_code == 200, result.text
        value = result.json()
        field = value["inspection"]["regions"][0]["form_evidence"]
        body = {**request, "preview_id": value["id"], "note": "Compared each original field and region.",
                "bindings": [{"widget_xref": field["xref"], "region_id": form_region(store.page(identifier, 1))["id"]}]}
        assert client.post(base + "/apply", headers=token, json={**body, "form_evidence": {}}).status_code == 422
        original = store.root / "originals" / f"{identifier}.pdf"
        before = original.read_bytes(); original.write_bytes(before + b"\nchanged")
        assert client.post(base + "/apply", headers=token, json=body).status_code == 409
        original.write_bytes(before)
        result = client.post(base + "/apply", headers=token, json=body)
        assert result.status_code == 200, result.text
        assert form_region(result.json())["form_evidence_changed"]


def test_inventory_loss_is_blocking_even_if_no_region_was_detected():
    page = {"regions": [], "form_evidence": {"inventory_complete": False, "widgets": [], "limitations": []}}
    assert form_inventory_blockers(page)
