"""Native table adoption is explicit, lossless, and bound to current revisions."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from uuid import uuid4

import fitz
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.review_routes import ReviewAPI
from app.core.config import Settings
from app.core.review import ReviewError
from app.core.structure_review import table_blockers
from app.infrastructure.review_store import ReviewStore
from app.infrastructure.structured_pdf import extract_page, inspect_pdf


NOTE = "Checked cell boundaries, blank entries, headers, and row relationships against the original."


@pytest.fixture
def table_workspace(tmp_path):
    store = ReviewStore(tmp_path / "workspace")
    identifier = str(uuid4())
    original = store.root / "originals" / f"{identifier}.pdf"
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((50, 35), "Before the table")
        for x in (50, 150, 250):
            page.draw_line((x, 80), (x, 180))
        for y in (80, 130, 180):
            page.draw_line((50, y), (250, y))
        page.insert_text((60, 105), "Name")
        page.insert_text((160, 105), "Amount")
        page.insert_text((60, 155), "Alice")
        page.insert_text((50, 250), "After the table")
        second = document.new_page(width=600, height=800)
        second.insert_text((50, 50), "Context on the next page")
        document.save(original)
    fingerprint = hashlib.sha256(original.read_bytes()).hexdigest()
    store.create_document(identifier, "ruled-table.pdf", fingerprint, inspect_pdf(str(original)))
    for number in (1, 2):
        page = extract_page(str(original), number)
        if number == 1:
            assert len(page["table_proposals"]["tables"]) == 1
            assert page["table_proposals"]["tables"][0]["status"] == "proposed"
            for source_region in page["regions"]:
                source_region["evidence"].append("Original native extraction retained by fixture.")
        else:
            page["coverage_approved"] = True
            for source_region in page["regions"]:
                source_region.update(source_approved=True, translation_approved=True,
                                     acknowledged={"test": "Existing review on another page."})
        with store.connection(True) as db:
            store._save_page(db, identifier, {**page, "revision": 1}, "test_extract", {})
    return store, identifier


def stored_page(store, identifier, number=1):
    with store.connection() as db:
        return store._page(db, identifier, number)


def save_fault(store, identifier, mutate):
    with store.connection(True) as db:
        page = store._page(db, identifier, 1)
        mutate(page)
        store._save_page(db, identifier, page, "injected_fault", {})
    return stored_page(store, identifier)


def review(store, identifier, action, note=NOTE):
    page = store.page(identifier, 1)
    table_id = page["table_proposals"]["tables"][0]["id"]
    return store.review_table(identifier, 1, page["revision"], table_id, action, note, "Bilingual reviewer")


def inside(region, box):
    r = region["bbox"]
    return box[0] <= r[0] and box[1] <= r[1] and r[2] <= box[2] and r[3] <= box[3]


def test_adoption_preserves_every_word_original_evidence_and_blank_cell(table_workspace):
    store, identifier = table_workspace
    original = stored_page(store, identifier)
    table = original["table_proposals"]["tables"][0]
    original_file = (store.root / "originals" / f"{identifier}.pdf").read_bytes()
    doc_revision = store.document(identifier)["revision"]
    review(store, identifier, "adopt")
    saved = stored_page(store, identifier)
    adopted = saved["table_proposals"]["tables"][0]
    assert adopted["original_regions"] == [r for r in original["regions"] if inside(r, table["bbox"])]
    original_words = Counter(" ".join(r["raw"] for r in original["regions"]).split())
    saved_words = Counter(" ".join(r["raw"] for r in saved["regions"]).split())
    assert saved_words == original_words
    assert [r["order"] for r in saved["regions"]] == list(range(len(saved["regions"])))
    assert saved["regions"][0]["source"] == "Before the table"
    assert saved["regions"][-1]["source"] == "After the table"
    members = [r for r in saved["regions"] if r.get("table_id") == table["id"]]
    assert len(members) == 4
    for r, cell in zip(members, table["cells"]):
        assert (r["cell_id"], r["bbox"], r["raw"], r["source"]) == (cell["id"], cell["bbox"], cell["raw"], cell["raw"])
        assert (r["row"], r["column"], r["row_span"], r["column_span"]) == (
            cell["row"], cell["column"], cell["row_span"], cell["column_span"])
        assert r["kind"] == "table_cell"
        assert not r["source_approved"] and not r["translation_approved"]
    blank = next(r for r in members if not r["raw"])
    assert (blank["row"], blank["column"]) == (1, 1)
    assert blank["disposition"] == "decorative" and blank["contains_text"] is False
    assert "check the original" in blank["reason"]
    assert not saved["coverage_approved"] and not adopted["structure_approved"]
    assert store.document(identifier)["revision"] == doc_revision + 1
    assert (store.root / "originals" / f"{identifier}.pdf").read_bytes() == original_file
    with store.connection() as db:
        event = json.loads(db.execute("SELECT body FROM events WHERE action='table_review'").fetchone()[0])
    assert event["table_id"] == table["id"] and event["action"] == "adopt"
    assert event["note"] == NOTE and event["reviewer"] == "Bilingual reviewer"


def test_adoption_and_structural_approval_are_separate_reviews(table_workspace):
    store, identifier = table_workspace
    assert table_blockers(store.page(identifier, 1))
    adopted = review(store, identifier, "adopt")
    assert table_blockers(adopted)
    assert any("Table 1" in issue for issue in adopted["blockers"])
    approved = review(store, identifier, "approve_structure")
    assert not table_blockers(approved)
    assert approved["table_proposals"]["tables"][0]["structure_review"] == {"note": NOTE, "reviewer": "Bilingual reviewer"}
    assert approved["blockers"]  # Structural approval does not approve content or page completeness.
    assert not approved["coverage_approved"]


def test_adoption_invalidates_other_page_context_approvals(table_workspace):
    store, identifier = table_workspace
    before = stored_page(store, identifier, 2)
    assert before["coverage_approved"] and before["regions"][0]["translation_approved"]
    review(store, identifier, "adopt")
    after = stored_page(store, identifier, 2)
    assert after["revision"] == before["revision"] + 1
    assert not after["coverage_approved"]
    assert all(not r["translation_approved"] and not r["acknowledged"] for r in after["regions"])
    assert all(r["source_approved"] for r in after["regions"])


@pytest.mark.parametrize("change", [{"source": "Corrected Name"}, {"translation": "नाम"}])
def test_cell_edits_invalidate_structural_review(table_workspace, change):
    store, identifier = table_workspace
    review(store, identifier, "adopt")
    page = review(store, identifier, "approve_structure")
    member = next(r for r in page["regions"] if r.get("table_id"))
    edited = store.mutate_region(identifier, 1, page["revision"], member["id"], change, "Editor")
    assert table_blockers(edited)
    assert not edited["table_proposals"]["tables"][0]["structure_approved"]
    assert all(next(r for r in edited["regions"] if r["id"] == member["id"])[key] == value for key, value in change.items())


@pytest.mark.parametrize("interleave", [False, True])
def test_reordering_requires_rechecking_contiguous_row_column_sequence(table_workspace, interleave):
    store, identifier = table_workspace
    review(store, identifier, "adopt")
    page = review(store, identifier, "approve_structure")
    original_ids = [r["id"] for r in page["regions"]]
    changed_ids = list(original_ids)
    if interleave:
        changed_ids.insert(2, changed_ids.pop(-1))
    else:
        changed_ids[1], changed_ids[2] = changed_ids[2], changed_ids[1]
    changed = store.reorder_regions(identifier, 1, page["revision"], changed_ids, "Order reviewer")
    assert table_blockers(changed)
    with pytest.raises(ReviewError) as error:
        review(store, identifier, "approve_structure")
    assert error.value.code == "table_reading_order"
    assert store.page(identifier, 1) == changed
    store.reorder_regions(identifier, 1, changed["revision"], original_ids, "Order reviewer")
    assert not table_blockers(review(store, identifier, "approve_structure"))


@pytest.mark.parametrize(("changes", "code"), [
    ({"source": "User's edited cell source"}, "table_existing_review"),
    ({"translation": "User's translated cell"}, "table_existing_review"),
    ({"source_approved": True}, "table_existing_review"),
    ({"translation_approved": True}, "table_existing_review"),
    ({"method": "image-ocr"}, "table_existing_review"),
    ({"bbox": [10, 85, 240, 115]}, "table_crossing_region"),
])
def test_adoption_refuses_existing_work_and_cross_boundary_paragraphs(table_workspace, changes, code):
    store, identifier = table_workspace
    def inject(page):
        table = page["table_proposals"]["tables"][0]
        target = next(r for r in page["regions"] if inside(r, table["bbox"]))
        target.update(changes)
    before = save_fault(store, identifier, inject)
    other_before = stored_page(store, identifier, 2)
    revision = store.document(identifier)["revision"]
    with pytest.raises(ReviewError) as error:
        review(store, identifier, "adopt")
    assert error.value.code == code
    assert stored_page(store, identifier) == before
    assert stored_page(store, identifier, 2) == other_before
    assert store.document(identifier)["revision"] == revision


@pytest.mark.parametrize(("fault", "code"), [
    ("mismatched_text", "table_content_mismatch"),
    ("ambiguous", "table_ambiguous"),
    ("outside_page", "table_cell_bounds"),
])
def test_unreliable_proposal_is_rejected_atomically(table_workspace, fault, code):
    store, identifier = table_workspace
    def inject(page):
        table = page["table_proposals"]["tables"][0]
        if fault == "mismatched_text":
            table["cells"][0]["raw"] += " hallucinated"
        elif fault == "ambiguous":
            table["status"] = "ambiguous"
        else:
            table["cells"][-1]["bbox"][2] = page["width"] + 1
    before = save_fault(store, identifier, inject)
    with pytest.raises(ReviewError) as error:
        review(store, identifier, "adopt")
    assert error.value.code == code
    assert stored_page(store, identifier) == before


def test_explicit_not_table_decision_preserves_native_regions(table_workspace):
    store, identifier = table_workspace
    before = stored_page(store, identifier)
    after = review(store, identifier, "not_table", "These ruled rectangles are not a semantic table; source paragraphs were checked.")
    assert not table_blockers(after)
    assert stored_page(store, identifier)["regions"] == before["regions"]
    assert not after["coverage_approved"]
    assert after["table_proposals"]["tables"][0]["decision"]["action"] == "not_table"


def test_adopted_cells_cannot_be_discarded_or_adopted_twice(table_workspace):
    store, identifier = table_workspace
    review(store, identifier, "adopt")
    before = stored_page(store, identifier)
    for action, code in (("adopt", "table_action"), ("not_table", "table_already_adopted")):
        with pytest.raises(ReviewError) as error:
            review(store, identifier, action)
        assert error.value.code == code
        assert stored_page(store, identifier) == before


def test_structural_approval_requires_all_cells_including_the_blank(table_workspace):
    store, identifier = table_workspace
    review(store, identifier, "adopt")
    def remove_blank(page):
        page["regions"] = [r for r in page["regions"] if not (r.get("table_id") and not r["source"])]
    before = save_fault(store, identifier, remove_blank)
    with pytest.raises(ReviewError) as error:
        review(store, identifier, "approve_structure")
    assert error.value.code == "table_cells_missing"
    assert stored_page(store, identifier) == before


def test_stale_api_table_review_does_not_overwrite_the_current_decision(table_workspace):
    store, identifier = table_workspace
    service = ReviewAPI(Settings(data_dir=store.root))
    app = FastAPI()
    service.install(app)
    page = store.page(identifier, 1)
    table_id = page["table_proposals"]["tables"][0]["id"]
    path = f"/api/v2/documents/{identifier}/pages/1/tables/{table_id}/review"
    payload = {"revision": page["revision"], "action": "adopt", "note": NOTE, "reviewer": "API reviewer"}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post(path, json=payload).status_code == 403
        headers = {"X-Review-Token": client.get("/api/v2/session").json()["token"]}
        response = client.post(path, json=payload, headers=headers)
        assert response.status_code == 200, response.text
        saved = deepcopy(response.json())
        stale = client.post(path, json={**payload, "action": "approve_structure"}, headers=headers)
        assert stale.status_code == 409
        assert stale.json()["code"] == "revision_conflict"
        assert store.page(identifier, 1) == saved
        short_note = client.post(path, json={**payload, "revision": saved["revision"], "note": "checked"}, headers=headers)
        assert short_note.status_code == 422
        approved = client.post(path, json={**payload, "revision": saved["revision"], "action": "approve_structure"}, headers=headers)
        assert approved.status_code == 200, approved.text
        assert not table_blockers(approved.json())


@pytest.mark.parametrize("empty_failure", [False, True])
def test_detection_warnings_need_a_current_specific_completeness_review(table_workspace, empty_failure):
    store, identifier = table_workspace
    review(store, identifier, "adopt")
    assert not table_blockers(review(store, identifier, "approve_structure"))
    def inject(page):
        proposal = page["table_proposals"]
        proposal["status"] = "failed" if empty_failure else "proposed"
        proposal["warnings"] = [] if empty_failure else [{"code": "unassigned_ruled_cells", "message": "Two ruled cells were not assigned to a table."}]
    before = save_fault(store, identifier, inject)
    assert table_blockers(before)
    for notes in ({}, {"structure_warnings": "checked"}, {"structure_warnings": " " * 15}):
        with pytest.raises(ReviewError) as error:
            store.approve(identifier, 1, before["revision"], "coverage", "Warning reviewer", acknowledgements=notes)
        assert error.value.code == "structure_warning_review"
        assert stored_page(store, identifier) == before
    approved = store.approve(identifier, 1, before["revision"], "coverage", "Warning reviewer",
        acknowledgements={"structure_warnings": "Compared every unmatched rectangle with the original; all visible text remains accounted for."})
    assert approved["coverage_approved"] and not table_blockers(approved)
    first_review = approved["structure_warning_review"]
    assert first_review["reviewer"] == "Warning reviewer"
    assert len(first_review["fingerprint"]) == 64

    def new_warning(page):
        page["table_proposals"]["warnings"].append({"code": "table_structure_failed", "message": "A different region now requires manual checking."})
    changed = save_fault(store, identifier, new_warning)
    assert table_blockers(changed), "An earlier acknowledgement must not cover changed warnings."
    assert changed["structure_warning_review"] == first_review
    second = store.approve(identifier, 1, changed["revision"], "coverage", "Second reviewer",
        acknowledgements={"structure_warnings": "Reviewed the newly identified region against the original and retained its content."})
    assert not table_blockers(second)
    assert second["structure_warning_review"]["fingerprint"] != first_review["fingerprint"]
