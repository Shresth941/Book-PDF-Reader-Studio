"""Cache and durable-page qualification; all artifacts stay under pytest tmp_path.

Native PDF tests deliberately avoid OCR, worker processes and model inference.
Their coverage proves bookkeeping and native text retention, not scan quality or
a reviewed 1,000-page translation workload.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
from pathlib import Path
from uuid import uuid4

import fitz
import pytest

from app.core.cache import TimedLruCache
from app.core.review import ReviewError
from app.infrastructure.pdf_reader import PyMuPdfReader
from app.infrastructure.review_store import ReviewStore
from app.infrastructure.structured_pdf import inspect_pdf, region
from app.services.document_service import DocumentService
from app.services.language_service import LibreTranslateService


def native_book(path, count):
    with fitz.open() as pdf:
        for number in range(1, count + 1):
            page = pdf.new_page(width=300, height=200)
            page.insert_text((25, 50), f"Qualification page {number:04d}.")
            if number % 37 == 0:
                page.set_rotation(90)
        pdf.save(path, deflate=True)


class PathStorage:
    def __init__(self, paths):
        self.paths = paths

    def path_for(self, identifier):
        return self.paths[identifier]


@pytest.mark.parametrize("count", [512, 513, 740, 1000])
def test_real_native_book_retains_every_page_after_eviction_and_reverse_reads(tmp_path, monkeypatch, count):
    path = tmp_path / f"native-{count}.pdf"
    native_book(path, count)
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    inspection = inspect_pdf(path)
    assert inspection["page_count"] == count
    assert [p["number"] for p in inspection["pages"]] == list(range(1, count + 1))

    def no_ocr(*args, **kwargs):
        pytest.fail("Synthetic native coverage must not invoke OCR or a heavy worker")

    monkeypatch.setattr(PyMuPdfReader, "_ocr_page", no_ocr)
    identifier = uuid4()
    service = DocumentService(PathStorage({identifier: path}), PyMuPdfReader())
    for start, end in [(1, count), (count, 1), (1, 5), (count, count - 4)]:
        pages = service.pages(identifier, start, end)
        assert pages == [{"page": number, "text": f"Qualification page {number:04d}."}
                         for number in range(min(start, end), max(start, end) + 1)]
        assert len(service._page_cache._items) <= 512
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash


def test_1001_page_inspection_rejects_without_modifying_original(tmp_path):
    path = tmp_path / "over-limit.pdf"
    native_book(path, 1001)
    before = path.read_bytes()
    with pytest.raises(ReviewError) as error:
        inspect_pdf(path)
    assert error.value.code == "page_limit"
    assert "1000" in error.value.message
    assert path.read_bytes() == before
    with fitz.open(path) as pdf:
        assert pdf.page_count == 1001


def test_durable_1000_page_inventory_survives_reopen_and_return_value_edits(tmp_path):
    store = ReviewStore(tmp_path / "inventory")
    identifier = str(uuid4())
    store.create_document(identifier, "inventory.pdf", "synthetic", {
        "status": "ready", "page_count": 1000,
        "pages": [{"number": n, "width": 300, "height": 200} for n in range(1, 1001)],
    })
    for number in [1, 512, 513, 740, 1000]:
        page = store.page(identifier, number)
        page["regions"].append({"source": "Caller changes are not a write"})
    reopened = ReviewStore(store.root)
    snapshot = reopened.snapshot(identifier, reopened.document(identifier)["revision"])
    assert [p["number"] for p in snapshot["pages"]] == list(range(1, 1001))
    assert all(p["status"] == "pending" and p["regions"] == [] for p in snapshot["pages"])
    assert snapshot["blockers"]
    assert reopened.document(identifier)["progress"] == {
        "extracted": 0, "coverage_approved": 0, "content_approved": 0,
    }


def test_cache_lru_hit_changes_eviction_order_and_replacement_is_current():
    cache = TimedLruCache(2)
    cache.set("a", "first")
    cache.set("b", "second")
    assert cache.get("a") == "first"
    cache.set("c", "third")
    assert cache.get("b") is None
    cache.set("a", "updated")
    assert cache.get("a") == "updated"
    assert cache.get("c") == "third"


def test_cache_ttl_expiry_does_not_refresh_on_reads_and_set_starts_new_lifetime(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.core.cache.monotonic", lambda: clock[0])
    cache = TimedLruCache(2, ttl_seconds=10)
    cache.set("page", "old")
    clock[0] = 109.0
    assert cache.get("page") == "old"
    clock[0] = 110.1
    assert cache.get("page") is None
    cache.set("page", "new")
    clock[0] = 119.0
    assert cache.get("page") == "new"
    clock[0] = 120.2
    assert cache.get("page") is None


def test_page_cache_isolated_by_document_and_returned_values_are_copies():
    first, second = uuid4(), uuid4()

    class Reader:
        def extract(self, path, start, end):
            return [{"page": n, "text": f"{path.stem}:{n}"} for n in range(start, end + 1)]

    service = DocumentService(PathStorage({first: Path("first.pdf"), second: Path("second.pdf")}), Reader())
    returned = service.pages(first, 1, 1)
    returned[0]["text"] = "An unsaved caller edit"
    returned.clear()
    assert service.pages(second, 1, 1) == [{"page": 1, "text": "second:1"}]
    assert service.pages(first, 1, 1) == [{"page": 1, "text": "first:1"}]


def test_partial_extraction_failure_retries_only_missing_pages():
    calls = []

    class Reader:
        def extract(self, path, start, end):
            calls.append((start, end))
            if len(calls) == 1:
                return [{"page": 1, "text": "One"}, {"page": 3, "text": "Three"}]
            return [{"page": 2, "text": "Two"}]

    identifier = uuid4()
    service = DocumentService(PathStorage({identifier: Path("unused.pdf")}), Reader())
    with pytest.raises(ValueError, match="every requested page"):
        service.pages(identifier, 1, 3)
    assert service.pages(identifier, 3, 1) == [
        {"page": 1, "text": "One"}, {"page": 2, "text": "Two"}, {"page": 3, "text": "Three"},
    ]
    assert calls == [(1, 3), (2, 2)]


def test_reader_exception_retains_committed_cache_entries_but_does_not_return_success():
    calls = []

    class Reader:
        def extract(self, path, start, end):
            calls.append((start, end))
            for number in range(start, end + 1):
                if len(calls) == 1 and number == 2:
                    raise ValueError("Deliberate extraction interruption")
                yield {"page": number, "text": f"Page {number}"}

    identifier = uuid4()
    service = DocumentService(PathStorage({identifier: Path("unused.pdf")}), Reader())
    with pytest.raises(ValueError, match="interruption"):
        service.pages(identifier, 1, 3)
    assert service.pages(identifier, 1, 3) == [{"page": n, "text": f"Page {n}"} for n in (1, 2, 3)]
    assert calls == [(1, 3), (2, 3)]


def test_concurrent_cache_eviction_never_changes_request_owned_results():
    identifiers = [uuid4() for _ in range(4)]

    class Reader:
        def extract(self, path, start, end):
            return [{"page": n, "text": f"{path.stem}:{n}"} for n in range(start, end + 1)]

    service = DocumentService(PathStorage({i: Path(f"{i}.pdf") for i in identifiers}), Reader())

    def read(identifier):
        for start, end in [(1, 740), (740, 1), (1, 10)]:
            assert service.pages(identifier, start, end) == [
                {"page": n, "text": f"{identifier}:{n}"}
                for n in range(min(start, end), max(start, end) + 1)
            ]

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(read, identifiers))
    assert len(service._page_cache._items) <= 512


def test_legacy_translation_cache_separates_direction_and_domain_without_network(monkeypatch):
    calls = []
    service = LibreTranslateService(None, None)

    async def translate_chunk(client, text, source, target):
        calls.append((text, source, target))
        return f"Draft {len(calls)}"

    monkeypatch.setattr(service, "_translate_chunk", translate_chunk)

    async def exercise():
        first = await service.translate("An ordinary sentence.", "en", "hi", "A book of stories")
        assert await service.translate("An ordinary sentence.", "en", "hi", "A book of stories") == first
        legal = await service.translate("An ordinary sentence.", "en", "hi", "Legal contract")
        reverse = await service.translate(first, "hi", "en", "A book of stories")
        assert legal != first
        assert reverse != "An ordinary sentence."

    asyncio.run(exercise())
    assert len(calls) == 3


@pytest.fixture
def checkpoint_store(tmp_path):
    store = ReviewStore(tmp_path / "checkpoints")
    identifier = str(uuid4())
    store.create_document(identifier, "context.pdf", "synthetic", {
        "status": "ready", "page_count": 1,
        "pages": [{"number": 1, "width": 300, "height": 200}],
    })
    page = store.page(identifier, 1)
    page.update(status="extracted", regions=[region(
        "paragraph", [20, 20, 200, 60], "The charge is defined here.",
        source_language="en", source_approved=True, translation="Saved draft", model_revision="old-model",
    )])
    with store.connection(True) as db:
        store._save_page(db, identifier, page, "fixture", {})
    job = store.enqueue(identifier, "translate", {"pages": [1]})
    with store.connection(True) as db:
        db.execute("UPDATE jobs SET status='running',attempt=1 WHERE id=?", (job["id"],))
    return store, identifier, store.job(job["id"])


@pytest.mark.parametrize("change", ["target", "domain", "jurisdiction", "glossary"])
def test_context_and_glossary_changes_reject_old_translation_results(checkpoint_store, change):
    store, identifier, job = checkpoint_store
    captured_document = store.document(identifier)
    captured_page = store.page(identifier, 1)
    result = deepcopy(captured_page)
    result["regions"][0].update(translation="Late stale translation", model_revision="new-model")
    if change == "glossary":
        store.add_glossary(identifier, captured_document["revision"], {
            "source": "charge", "target": "आरोप", "sense": "legal accusation", "reference": "Document definition",
        })
    else:
        context = {"target": "hi", "domain": "general", "jurisdiction": ""}
        context[change] = {"target": "en", "domain": "legal", "jurisdiction": "India"}[change]
        store.update_context(identifier, captured_document["revision"], context)
    current = store.page(identifier, 1)
    with pytest.raises(ReviewError) as error:
        store.checkpoint(job["id"], job["attempt"], 1, captured_page["revision"], result, captured_document["revision"])
    assert error.value.code == "revision_conflict"
    assert store.page(identifier, 1) == current
    assert store.job(job["id"])["cursor"] == 0


def test_restart_rejects_old_attempt_and_new_attempt_publishes_exactly_once(checkpoint_store):
    store, identifier, job = checkpoint_store
    old_page = store.page(identifier, 1)
    result = deepcopy(old_page)
    result["regions"][0].update(translation="Fresh model draft", model_revision="new-model")
    store.recover()
    with pytest.raises(ReviewError) as error:
        store.checkpoint(job["id"], job["attempt"], 1, old_page["revision"], result)
    assert error.value.code == "superseded"
    assert store.page(identifier, 1) == old_page
    resumed = store.control(job["id"], "resume")
    with store.connection(True) as db:
        db.execute("UPDATE jobs SET status='running',attempt=attempt+1 WHERE id=?", (job["id"],))
    resumed = store.job(job["id"])
    store.checkpoint(job["id"], resumed["attempt"], 1, old_page["revision"], result)
    published = store.page(identifier, 1)
    assert published["regions"][0]["model_revision"] == "new-model"
    assert published["regions"][0]["translation"] == "Fresh model draft"
    with pytest.raises(ReviewError) as error:
        store.checkpoint(job["id"], resumed["attempt"], 1, old_page["revision"], result)
    assert error.value.code == "revision_conflict"
    assert store.page(identifier, 1) == published
    assert store.job(job["id"])["cursor"] == 1
