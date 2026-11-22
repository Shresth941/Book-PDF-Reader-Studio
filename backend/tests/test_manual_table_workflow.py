"""Revision-safe manual grids, review gates, and PDF/DOCX output integration."""
from copy import deepcopy
from pathlib import Path

from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
import fitz
import pytest

from app.api.review_routes import ReviewAPI
from app.core.config import Settings
from app.core.review import ReviewError
from app.services.review_export import build_export
from test_structure_review import table_workspace, stored_page, inside, NOTE


def body_for(page):
    table = page["table_proposals"]["tables"][0]
    return {"x_edges": [50, 150, 250], "y_edges": [80, 130, 180], "merges": [],
            "replace_region_ids": [r["id"] for r in page["regions"] if inside(r, table["bbox"])],
            "replace_table_id": table["id"], "note": NOTE}


def test_manual_grid_transaction_invalidates_dependents_and_rejects_replay(table_workspace):
    store, identifier = table_workspace
    before = stored_page(store, identifier)
    other = stored_page(store, identifier, 2)
    revision = store.document(identifier)["revision"]
    saved = store.create_manual_table(identifier, 1, before["revision"], body_for(before), "Reviewer")
    assert saved["revision"] == before["revision"] + 1
    assert store.document(identifier)["revision"] == revision + 1
    assert saved["table_proposals"]["tables"][0]["original_proposal"] == before["table_proposals"]["tables"][0]
    assert stored_page(store, identifier, 2)["revision"] == other["revision"] + 1
    assert not stored_page(store, identifier, 2)["coverage_approved"]
    assert stored_page(store, identifier, 2)["regions"][0]["source"] == other["regions"][0]["source"]
    with pytest.raises(ReviewError) as error:
        store.create_manual_table(identifier, 1, before["revision"], body_for(before), "Reviewer")
    assert error.value.code == "revision_conflict"
    assert store.page(identifier, 1) == saved
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM events WHERE action='manual_table_create'").fetchone()[0] == 1


def test_invalid_mapping_preserves_both_pages_and_document_revision(table_workspace):
    store, identifier = table_workspace
    before, other, doc = [stored_page(store, identifier), stored_page(store, identifier, 2), store.document(identifier)]
    body = body_for(before); body["replace_region_ids"] = []
    with pytest.raises(ReviewError): store.create_manual_table(identifier, 1, before["revision"], body, "Reviewer")
    assert stored_page(store, identifier) == before
    assert stored_page(store, identifier, 2) == other
    assert store.document(identifier) == doc


def test_api_requires_auth_revision_and_bounded_schema(table_workspace):
    store, identifier = table_workspace
    service = ReviewAPI(Settings(data_dir=store.root, minimum_free_memory_mb=0))
    service.store = store
    app = FastAPI(); service.install(app)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get('/api/v2/capabilities').json()['structure']['manual_tables'] is True
        page = store.page(identifier, 1)
        body = {**body_for(page), "revision": page["revision"], "reviewer": "Tester"}
        endpoint = f"/api/v2/documents/{identifier}/pages/1/tables/manual"
        assert client.post(endpoint, json=body).status_code == 403
        headers = {"X-Review-Token": client.get('/api/v2/session').json()["token"]}
        for invalid in ({"x_edges": list(range(22))}, {"x_edges": [False, 150, 250]}, {"source_approved": True}, {"merges": [{"row": True, "column": 0, "row_span": 1, "column_span": 1}]}):
            assert client.post(endpoint, json={**body, **invalid}, headers=headers).status_code == 422
        response = client.post(endpoint, json=body, headers=headers)
        assert response.status_code == 201, response.text
        assert response.json()["blockers"]
        assert client.post(endpoint, json=body, headers=headers).status_code == 409


def test_reviewed_manual_grid_exports_without_duplicating_archived_source(table_workspace, tmp_path):
    store, identifier = table_workspace
    doc = store.document(identifier)
    store.update_context(identifier, doc["revision"], {"target": "en", "domain": "general", "jurisdiction": ""})
    before = store.page(identifier, 1)
    original = store.root / "originals" / f"{identifier}.pdf"
    original_bytes = original.read_bytes()
    saved = store.create_manual_table(identifier, 1, before["revision"], body_for(before), "Reviewer")
    assert store.snapshot(identifier, store.document(identifier)["revision"])["blockers"]
    table_id = saved["table_proposals"]["tables"][0]["id"]
    values = {(0, 0): "Name", (0, 1): "Amount", (1, 0): "Alice", (1, 1): ""}
    # Simulates a reviewer transcribing every visible cell, including one blank.
    for number in (1, 2):
        for item in store.page(identifier, number)["regions"]:
            text = values[(item["row"], item["column"])] if item.get("table_id") else item["source"]
            page = store.page(identifier, number)
            store.mutate_region(identifier, number, page["revision"], item["id"], {
                "source": text, "source_language": "en", "disposition": "already_target" if text else "decorative",
                "contains_text": bool(text), "reason": "Compared the original: this cell is blank." if not text else ""}, "Reviewer")
    for number in (1, 2):
        for item in store.page(identifier, number)["regions"]:
            page = store.page(identifier, number)
            store.approve(identifier, number, page["revision"], "source", "Reviewer", item["id"])
    page = store.page(identifier, 1)
    store.review_table(identifier, 1, page["revision"], table_id, "approve_structure", NOTE, "Reviewer")
    for number in (1, 2):
        page = store.page(identifier, number)
        store.approve(identifier, number, page["revision"], "coverage", "Reviewer")
    snapshot = store.snapshot(identifier, store.document(identifier)["revision"])
    assert not snapshot["blockers"]
    pdf_path = tmp_path / "manual.pdf"
    result = build_export(snapshot, str(original), str(pdf_path), "pdf", "readable")
    assert result["text_verified"] and result["render_verified"]
    with fitz.open(pdf_path) as document:
        text = "\n".join(p.get_text() for p in document)
    for word in ("Name", "Amount", "Alice"):
        assert text.count(word) == 1
    mapped = {entry["expected"]: entry for entry in result["content_map"]}
    assert mapped["Name"]["spans"][0]["bbox"][0] < mapped["Amount"]["spans"][0]["bbox"][0]
    assert mapped["Alice"]["spans"][0]["bbox"][1] > mapped["Name"]["spans"][0]["bbox"][1]
    docx_path = tmp_path / "manual.docx"
    build_export(snapshot, str(original), str(docx_path), "docx", "readable")
    grid = Document(docx_path).tables[0]
    assert [[c.text for c in row.cells] for row in grid.rows] == [["Name", "Amount"], ["Alice", ""]]
    assert original.read_bytes() == original_bytes
