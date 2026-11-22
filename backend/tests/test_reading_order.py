"""Reading-order corrections retain evidence and invalidate dependent review."""
from copy import deepcopy
import json
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.review_routes import ReviewAPI
from app.core.config import Settings
from app.core.review import ReviewError
from app.infrastructure.review_store import ReviewStore
from app.infrastructure.structured_pdf import region
from app.services.review_export import text_export


@pytest.fixture
def workspace(tmp_path):
    store = ReviewStore(tmp_path / "workspace")
    identifier = str(uuid4())
    store.create_document(identifier, "reading-order.pdf", "a" * 64, {
        "status": "ready", "page_count": 2,
        "pages": [{"number": n, "width": 600, "height": 800} for n in (1, 2)],
    })
    with store.connection(True) as db:
        for n, texts in ((1, ["Heading", "Left column", "Right column"]), (2, ["Continuation"])):
            page = store._page(db, identifier, n)
            page.update(status="extracted", coverage_approved=True, regions=[
                region("paragraph", [10, 20 + 50 * i, 500, 60 + 50 * i], text,
                       order=i, source_language="en", source_approved=True,
                       translation=f"Translated {text}", translation_approved=True,
                       acknowledged={"review": "Approved before the order changed."},
                       alternatives=[{"method": "ocr", "text": f"Raw alternative {text}"}],
                       evidence=["Original source crop retained."], reason="A saved correction note.")
                for i, text in enumerate(texts)
            ])
            store._save_page(db, identifier, page, "fixture", {})
    return store, identifier


def test_reverse_order_preserves_all_content_evidence_and_audit(workspace):
    store, identifier = workspace
    original = store.page(identifier, 1)
    by_id = {r["id"]: deepcopy(r) for r in original["regions"]}
    requested = list(reversed(by_id))
    result = store.reorder_regions(identifier, 1, original["revision"], requested, "Bilingual reviewer")
    assert [r["id"] for r in result["regions"]] == requested
    assert [r["order"] for r in result["regions"]] == [0, 1, 2]
    changed_fields = {"order", "translation_approved", "acknowledged"}
    for saved in result["regions"]:
        before = by_id[saved["id"]]
        assert {k: v for k, v in saved.items() if k not in changed_fields} == {
            k: v for k, v in before.items() if k not in changed_fields
        }
    assert ReviewStore(store.root).page(identifier, 1)["regions"] == result["regions"]
    with store.connection() as db:
        event = json.loads(db.execute("SELECT body FROM events WHERE action='reading_order'").fetchone()[0])
    assert event == {"page": 1, "revision": result["revision"], "before": list(by_id),
                     "after": requested, "reviewer": "Bilingual reviewer"}


@pytest.mark.parametrize("invalid", ["duplicate", "missing", "foreign", "empty"])
def test_invalid_permutations_cannot_remove_or_replace_regions(workspace, invalid):
    store, identifier = workspace
    before = store.page(identifier, 1)
    ids = [r["id"] for r in before["regions"]]
    candidates = {"duplicate": [ids[0], ids[0], ids[2]], "missing": ids[:-1],
                  "foreign": [*ids[:-1], str(uuid4())], "empty": []}
    revision = store.document(identifier)["revision"]
    with pytest.raises(ReviewError) as error:
        store.reorder_regions(identifier, 1, before["revision"], candidates[invalid], "reviewer")
    assert error.value.code == "invalid_reading_order"
    assert store.page(identifier, 1) == before
    assert store.document(identifier)["revision"] == revision


def test_reorder_invalidates_dependent_approvals_on_every_page(workspace):
    store, identifier = workspace
    pages = [store.page(identifier, n) for n in (1, 2)]
    before_revision = store.document(identifier)["revision"]
    store.reorder_regions(identifier, 1, pages[0]["revision"],
                          [r["id"] for r in reversed(pages[0]["regions"])], "reviewer")
    assert store.document(identifier)["revision"] == before_revision + 1
    for old in pages:
        saved = store.page(identifier, old["number"])
        assert saved["revision"] == old["revision"] + 1
        assert not saved["coverage_approved"]
        assert all(r["source_approved"] for r in saved["regions"])
        assert all(not r["translation_approved"] and not r["acknowledged"] for r in saved["regions"])


def test_stale_reorder_cannot_overwrite_a_saved_source_edit(workspace):
    store, identifier = workspace
    before = store.page(identifier, 1)
    changed = store.mutate_region(identifier, 1, before["revision"], before["regions"][0]["id"],
                                  {"source": "A newer corrected heading"}, "reviewer")
    with pytest.raises(ReviewError) as error:
        store.reorder_regions(identifier, 1, before["revision"],
                              [r["id"] for r in reversed(before["regions"])], "stale browser")
    assert error.value.status == 409
    assert store.page(identifier, 1) == changed


def test_saved_order_rejects_prior_worker_results_and_export_snapshot(workspace):
    store, identifier = workspace
    before = store.page(identifier, 1)
    doc_revision = store.document(identifier)["revision"]
    job = store.enqueue(identifier, "translate", {"pages": [1]})
    with store.connection(True) as db:
        db.execute("UPDATE jobs SET status='running',attempt=1 WHERE id=?", (job["id"],))
    saved = store.reorder_regions(identifier, 1, before["revision"],
                                  [r["id"] for r in reversed(before["regions"])], "reviewer")
    with pytest.raises(ReviewError) as worker_error:
        store.checkpoint(job["id"], 1, 1, before["revision"], before, doc_revision)
    with pytest.raises(ReviewError) as export_error:
        store.snapshot(identifier, doc_revision)
    assert worker_error.value.status == export_error.value.status == 409
    assert store.job(job["id"])["cursor"] == 0
    assert store.page(identifier, 1) == saved


def test_identical_order_does_not_discard_approvals(workspace):
    store, identifier = workspace
    before = store.page(identifier, 1)
    doc_revision = store.document(identifier)["revision"]
    result = store.reorder_regions(identifier, 1, before["revision"],
                                    [r["id"] for r in before["regions"]], "reviewer")
    assert result == before
    assert store.document(identifier)["revision"] == doc_revision


def test_export_uses_saved_order_without_omitting_or_duplicating_content(workspace):
    store, identifier = workspace
    before = store.page(identifier, 1)
    saved = store.reorder_regions(identifier, 1, before["revision"],
                                  [r["id"] for r in reversed(before["regions"])], "reviewer")
    snapshot = store.snapshot(identifier, store.document(identifier)["revision"])
    output = text_export(snapshot)
    expected = [r["translation"] for r in saved["regions"]]
    offsets = [output.index(text) for text in expected]
    assert offsets == sorted(offsets)
    assert all(output.count(text) == 1 for text in expected)
    assert "Translated Continuation" in output


def test_reading_order_endpoint_requires_token_and_expected_revision(workspace):
    store, identifier = workspace
    api = ReviewAPI(Settings(data_dir=store.root))
    app = FastAPI()
    api.install(app)
    before = store.page(identifier, 1)
    payload = {"revision": before["revision"], "reviewer": "API reviewer",
               "region_ids": [r["id"] for r in reversed(before["regions"])]}
    path = f"/api/v2/documents/{identifier}/pages/1/reading-order"
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post(path, json=payload).status_code == 403
        headers = {"X-Review-Token": client.get("/api/v2/session").json()["token"]}
        response = client.post(path, json=payload, headers=headers)
        assert response.status_code == 200, response.text
        assert [r["id"] for r in response.json()["regions"]] == payload["region_ids"]
        assert client.post(path, json=payload, headers=headers).status_code == 409
        assert client.post(path, json={**payload, "region_ids": ["not-an-id"]}, headers=headers).status_code == 422
