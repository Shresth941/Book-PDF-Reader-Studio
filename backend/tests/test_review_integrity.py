"""Regression cases for manual-content preservation and context-bound approvals."""
from copy import deepcopy
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.review import ReviewError, region_blockers
from app.infrastructure.review_store import ReviewStore
from app.infrastructure.structured_pdf import region
from app.services.review_jobs import JobRunner


@pytest.fixture
def reviewed_store(tmp_path):
    store = ReviewStore(tmp_path / "review")
    identifier = str(uuid4())
    store.create_document(identifier, "source.pdf", "fixture-fingerprint", {
        "status": "ready", "page_count": 2,
        "pages": [{"number": n, "width": 600, "height": 800} for n in (1, 2)],
    })
    for number in (1, 2):
        page = store.page(identifier, number)
        page.update(status="extracted", coverage_approved=True, regions=[
            region("paragraph", [20, 20 + 60 * i, 400, 65 + 60 * i], "Original meaning",
                   order=i, source_language="en", translation="मूल अर्थ",
                   source_approved=True, translation_approved=True,
                   acknowledged={"review_note": "Verified in the containing section."})
            for i in range(2)
        ])
        with store.connection(True) as db:
            store._save_page(db, identifier, page, "fixture", {})
    assert not store.snapshot(identifier, store.document(identifier)["revision"])["blockers"]
    return store, identifier


def running_extraction(store, identifier):
    job = store.enqueue(identifier, "extract", {"pages": [1]})
    with store.connection(True) as db:
        db.execute("UPDATE jobs SET status='running',attempt=1 WHERE id=?", (job["id"],))
    return store.job(job["id"])


def pending_page(store, identifier, regions):
    page = store.page(identifier, 1)
    page.update(status="pending", coverage_approved=False, regions=regions)
    with store.connection(True) as db:
        store._save_page(db, identifier, page, "fixture_pending", {})
    return store.page(identifier, 1)


def test_extraction_pauses_without_touching_pending_manual_work(reviewed_store):
    store, identifier = reviewed_store
    pending_page(store, identifier, [])
    page = store.add_region(identifier, 1, store.page(identifier, 1)["revision"],
                            {"bbox": [20, 20, 200, 100]}, "reviewer")
    page = store.mutate_region(identifier, 1, page["revision"], page["regions"][0]["id"],
                               {"source": "Manually recovered clause", "translation": "सहेजा गया पाठ"}, "reviewer")
    saved = deepcopy(page)

    class WorkerMustNotRun:
        def run(self, *args):
            pytest.fail("Extraction must not start over manual regions")

    job = running_extraction(store, identifier)
    JobRunner(store, WorkerMustNotRun(), Settings(data_dir=store.root)).run(job)
    assert store.page(identifier, 1) == saved
    assert store.job(job["id"])["status"] == "paused"
    assert "existing_page_content" in store.job(job["id"])["message"]
    assert store.job(job["id"])["cursor"] == 0


def test_extraction_checkpoint_also_refuses_to_replace_existing_regions(reviewed_store):
    store, identifier = reviewed_store
    page = pending_page(store, identifier, store.page(identifier, 1)["regions"])
    job = running_extraction(store, identifier)
    with pytest.raises(ReviewError) as error:
        store.checkpoint(job["id"], job["attempt"], 1, page["revision"],
                         {**page, "regions": [], "status": "extracted"})
    assert error.value.code == "existing_page_content"
    assert store.page(identifier, 1) == page
    assert store.job(job["id"])["cursor"] == 0


def test_extraction_still_publishes_a_pristine_pending_page(reviewed_store):
    store, identifier = reviewed_store
    pending_page(store, identifier, [])
    extracted = region("paragraph", [20, 20, 300, 80], "Newly extracted text")

    class Worker:
        def run(self, *args):
            return {"status": "extracted", "regions": [extracted], "coverage_approved": False}

    job = running_extraction(store, identifier)
    JobRunner(store, Worker(), Settings(data_dir=store.root)).run(job)
    assert store.job(job["id"])["status"] == "completed"
    assert store.page(identifier, 1)["regions"][0]["source"] == "Newly extracted text"


def test_manual_edit_arriving_during_extraction_wins(reviewed_store):
    store, identifier = reviewed_store
    pending_page(store, identifier, [])

    class Worker:
        def run(self, *args):
            page = store.page(identifier, 1)
            added = store.add_region(identifier, 1, page["revision"], {"bbox": [20, 20, 200, 100]}, "reviewer")
            store.mutate_region(identifier, 1, added["revision"], added["regions"][0]["id"],
                                {"source": "Concurrent user edit"}, "reviewer")
            return {"status": "extracted", "regions": [region("paragraph", [20, 20, 200, 100], "Old worker result")]}

    job = running_extraction(store, identifier)
    JobRunner(store, Worker(), Settings(data_dir=store.root)).run(job)
    assert store.page(identifier, 1)["regions"][0]["source"] == "Concurrent user edit"
    assert store.job(job["id"])["status"] == "paused"
    assert "revision_conflict" in store.job(job["id"])["message"]


@pytest.mark.parametrize("change", [
    {"source": "Changed definition"}, {"source_language": "hi"},
    {"disposition": "excluded"}, {"kind": "footnote"}, {"contains_text": False},
])
def test_context_edit_invalidates_siblings_and_other_pages(reviewed_store, change):
    store, identifier = reviewed_store
    page = store.page(identifier, 1)
    sibling = deepcopy(page["regions"][1])
    store.mutate_region(identifier, 1, page["revision"], page["regions"][0]["id"], change, "reviewer")
    for number in (1, 2):
        current = store.page(identifier, number)
        assert not current["coverage_approved"]
        assert all(not r["translation_approved"] and not r["acknowledged"] for r in current["regions"])
    current_sibling = store.page(identifier, 1)["regions"][1]
    assert current_sibling["source"] == sibling["source"]
    assert current_sibling["translation"] == sibling["translation"]
    assert current_sibling["source_approved"]
    assert store.snapshot(identifier, store.document(identifier)["revision"])["blockers"]


def test_new_region_invalidates_existing_sibling_translations(reviewed_store):
    store, identifier = reviewed_store
    page = store.page(identifier, 1)
    updated = store.add_region(identifier, 1, page["revision"], {"bbox": [20, 200, 200, 260]}, "reviewer")
    assert len(updated["regions"]) == 3
    assert all(not r["translation_approved"] and not r["acknowledged"] for r in updated["regions"])
    assert not store.page(identifier, 2)["coverage_approved"]


def test_translation_only_edit_keeps_unaffected_source_and_sibling_approval(reviewed_store):
    store, identifier = reviewed_store
    page = store.page(identifier, 1)
    updated = store.mutate_region(identifier, 1, page["revision"], page["regions"][0]["id"],
                                  {"translation": "संशोधित अनुवाद"}, "reviewer")
    assert not updated["regions"][0]["translation_approved"]
    assert updated["regions"][1]["translation_approved"]
    assert store.page(identifier, 2)["coverage_approved"]


@pytest.mark.parametrize("language", ["en", "uncertain", "mixed"])
def test_already_target_requires_a_confirmed_matching_language(reviewed_store, language):
    store, identifier = reviewed_store
    page = store.page(identifier, 1)
    candidate = page["regions"][0]
    candidate.update(disposition="already_target", source_language=language, source_approved=True)
    with store.connection(True) as db:
        store._save_page(db, identifier, page, "fixture_legacy_approval", {})
    current = store.page(identifier, 1)
    assert any("document target" in issue for issue in current["blockers"])
    assert store.document(identifier)["progress"]["content_approved"] == 1
    with pytest.raises(ReviewError) as error:
        store.approve(identifier, 1, current["revision"], "source", "reviewer", candidate["id"])
    assert error.value.code == "already_target_language"
    assert store.snapshot(identifier, store.document(identifier)["revision"])["blockers"]


def test_target_change_invalidates_already_target_even_after_switching_back(reviewed_store):
    store, identifier = reviewed_store
    page = store.page(identifier, 1)
    candidate = page["regions"][0]
    candidate.update(disposition="already_target", source_language="hi", source="मूल अर्थ", source_approved=False)
    with store.connection(True) as db:
        store._save_page(db, identifier, page, "fixture_target_region", {})
    page = store.approve(identifier, 1, store.page(identifier, 1)["revision"], "source", "reviewer", candidate["id"])
    assert not page["blockers"]
    store.update_context(identifier, store.document(identifier)["revision"], {"target": "en", "domain": "general", "jurisdiction": ""})
    changed = store.page(identifier, 1)
    assert not changed["regions"][0]["source_approved"]
    with pytest.raises(ReviewError, match="matching the document target"):
        store.approve(identifier, 1, changed["revision"], "source", "reviewer", candidate["id"])
    store.update_context(identifier, store.document(identifier)["revision"], {"target": "hi", "domain": "general", "jurisdiction": ""})
    assert not store.page(identifier, 1)["regions"][0]["source_approved"]


def test_already_target_cannot_be_certified_without_document_context():
    candidate = region("paragraph", [0, 0, 100, 100], "मूल अर्थ", disposition="already_target",
                       source_language="hi", source_approved=True)
    assert region_blockers(candidate, [])
    assert not region_blockers(candidate, [], "hi")
