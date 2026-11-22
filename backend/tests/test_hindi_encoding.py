"""Legacy Hindi fonts must not be exposed as Unicode, or erase saved corrections."""
from copy import deepcopy

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.review_routes import ReviewAPI
from app.core.config import Settings
from app.core.review import ReviewError
from app.infrastructure import structured_pdf
from app.infrastructure.pdf_reader import PyMuPdfReader
from test_review_workflow import workspace, source_pdf


@pytest.mark.parametrize("font", ["XpressChanak", "CJBJNI+XpressChanak", "XpressChanakBold", "XpressChanakItalic", "Chanakya", "Shiva-Medium"])
def test_legacy_fonts_retain_raw_text_and_offer_unicode_ocr(source_pdf, monkeypatch, font):
    original = fitz.Page.get_text
    def legacy_text(page, *args, **kwargs):
        result = original(page, *args, **kwargs)
        if args and args[0] == "dict":
            for block in result["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        span["font"] = font
                        span["text"] += "Ô"  # Unmapped Xpress glyph must retain OCR fallback.
        return result
    monkeypatch.setattr(fitz.Page, "get_text", legacy_text)
    monkeypatch.setattr(structured_pdf, "ocr_region", lambda *a, **kw: {"method": "tesseract-original", "text": "भारत का संविधान"})
    result = structured_pdf.extract_page(source_pdf, 1)
    for region in result["regions"]:
        assert region["raw"]
        assert region["source"] == ""
        assert region["disposition"] == "unreadable"
        assert region["alternatives"][0]["text"] == "भारत का संविधान"
        assert not region["source_approved"]
    assert font in PyMuPdfReader._legacy_encoded_fonts({"blocks": [{"lines": [{"spans": [{"font": font, "text": "encoded text"}]}]}]})


def test_native_text_does_not_require_ocr(source_pdf, monkeypatch):
    monkeypatch.setattr(structured_pdf, "ocr_region", lambda *a, **kw: pytest.fail("Native text should stay native"))
    result = structured_pdf.extract_page(source_pdf, 1)
    assert "debtor" in result["regions"][0]["source"]
    assert not result["regions"][0]["alternatives"]


def test_real_unicode_hindi_remains_native(tmp_path, monkeypatch):
    from app.services.review_export import shaped_document, checked_pdf_output
    pdf = shaped_document(tmp_path)
    pdf.add_page(); pdf.multi_cell(0, 20, "भारत का संविधान")
    path = tmp_path / "unicode-hindi.pdf"
    path.write_bytes(checked_pdf_output(pdf))
    monkeypatch.setattr(structured_pdf, "ocr_region", lambda *a, **kw: pytest.fail("Valid Unicode Hindi should remain native"))
    result = structured_pdf.extract_page(path, 1)
    assert "भारत का संविधान" in " ".join(r["source"] for r in result["regions"])


def test_bad_ascenders_do_not_pull_previous_paragraph_into_ocr():
    block = {"lines": [{"spans": [{"bbox": [100, 185, 200, 221], "size": 13, "origin": [100, 219]}]}]}
    box = structured_pdf.legacy_recognition_box(block)
    assert box.y0 == 204
    assert box.x0 < 100 and box.x1 > 200  # Allow overhanging glyphs / matras.


def fresh_recognition(page):
    fresh = deepcopy(page)
    for r in fresh["regions"]:
        r.update(source="", disposition="unreadable", alternatives=[{"method": "tesseract-original", "text": "भारत का संविधान"}], evidence=["Native encoding is unreliable."])
    return fresh


def test_refresh_repairs_only_untouched_text_and_is_idempotent(workspace):
    store, identifier = workspace
    before = store.page(identifier, 1)
    fresh = fresh_recognition(before)
    result = store.refresh_legacy_recognition(identifier, 1, before["revision"], fresh, "tester")
    assert [r["id"] for r in result["regions"]] == [r["id"] for r in before["regions"]]
    assert [r["raw"] for r in result["regions"]] == [r["raw"] for r in before["regions"]]
    assert all(r["source"] == "" and r["disposition"] == "unreadable" for r in result["regions"])
    repeated = store.refresh_legacy_recognition(identifier, 1, result["revision"], fresh, "tester")
    assert repeated == result


def test_refresh_preserves_saved_corrections_and_rejects_stale_revision(workspace):
    store, identifier = workspace
    page = store.page(identifier, 1)
    fresh = fresh_recognition(page)
    region = page["regions"][0]
    edited = store.mutate_region(identifier, 1, page["revision"], region["id"],
                                {"source": "मेरी जाँची हुई पंक्ति", "translation": "My saved translation", "source_language": "hi", "reason": "Checked against original"}, "tester")
    with pytest.raises(ReviewError, match="changed"):
        store.refresh_legacy_recognition(identifier, 1, page["revision"], fresh, "tester")
    result = store.refresh_legacy_recognition(identifier, 1, edited["revision"], fresh, "tester")
    for field in ("source", "translation", "source_language", "reason", "id", "raw", "bbox"):
        assert result["regions"][0][field] == edited["regions"][0][field]
    assert result["regions"][0]["alternatives"]


def test_changed_or_ambiguous_geometry_never_replaces_region(workspace):
    store, identifier = workspace
    page = store.page(identifier, 1)
    fresh = fresh_recognition(page)
    fresh["regions"][0]["bbox"][0] += 1
    fresh["regions"].append(deepcopy(fresh["regions"][1]))
    assert store.refresh_legacy_recognition(identifier, 1, page["revision"], fresh, "tester") == page


def test_refresh_api_checks_token_fingerprint_and_revision(workspace, monkeypatch):
    store, identifier = workspace
    service = ReviewAPI(Settings(data_dir=store.root))
    app = FastAPI(); service.install(app)
    page = store.page(identifier, 1)
    calls = []
    def worker(operation, payload):
        calls.append(payload)
        assert operation == "extract"
        return fresh_recognition(page)
    monkeypatch.setattr(service.worker, "run", worker)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        path = f"/api/v2/documents/{identifier}/pages/1/recognition"
        assert client.post(path, json={"revision": page["revision"]}).status_code == 403
        headers = {"X-Review-Token": client.get("/api/v2/session").json()["token"]}
        assert client.post(path, headers=headers, json={"revision": page["revision"]}).status_code == 200
        assert calls[0]["document_sha256"] == store.document(identifier)["sha256"]
        assert client.post(path, headers=headers, json={"revision": page["revision"]}).status_code == 409


def test_recognition_refuses_changed_original(source_pdf):
    with pytest.raises(ReviewError, match="fingerprint changed"):
        structured_pdf.extract_page(source_pdf, 1, document_sha256="0" * 64)
