"""Exact text checks, based on visually transcribed source pages, not OCR output."""
import json
import os
from pathlib import Path

import fitz
import pytest

from app.infrastructure.legacy_hindi import decode_xpress, decode_block
from app.infrastructure import structured_pdf
from test_review_workflow import workspace, source_pdf

GOLDEN = json.loads((Path(__file__).parent / "fixtures/hindi-xpress-golden.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", GOLDEN, ids=["cover", "article-16", "matras-and-nukta"])
def test_exact_visible_text(case):
    assert decode_block(case["block"]) == case["expected"]


@pytest.mark.parametrize("raw, expected", [
    ("ÁŸÿÈÁÄÃ", "नियुक्ति"), ("◊Í‹ﬂ¢‡Ê", "मूलवंश"),
    ("œ◊¸", "धर्म"), ("ﬂªÙZ", "वर्गों"), ("ßZœŸ", "ईंधन"),
    ("Á¬¿«∏U", "पिछड़े"), ("vwy", "124"), ("v~z{", "1956"),
    ("wÆvz", "2015"), ("‚◊¤Ê", "समझे"), ("◊ÈÅÿ", "मुख्य"),
])
def test_words_and_numbers(raw, expected):
    assert decode_xpress(raw, "XpressChanak") == expected


def test_unknowns_and_other_encodings_are_not_guessed():
    assert decode_xpress("Ô", "XpressChanak") is None
    assert decode_xpress("भारत", "XpressChanak") is None
    assert decode_xpress("÷Ê⁄UÃ", "Chanakya") is None
    assert decode_xpress("÷Ê⁄UÃ", "UnknownXpressChanak") is None


def test_mixed_fonts_preserve_english_and_do_not_convert_its_digits():
    block = {"lines": [{"spans": [
        {"font": "Helvetica", "text": "Article 16: "},
        {"font": "ABCDEF+XpressChanakBold", "text": "÷Ê⁄UÃ"},
    ]}]}
    assert decode_block(block) == "Article 16: भारत"


def test_split_prebase_matra_is_reassembled_without_changing_evidence():
    from copy import deepcopy
    block = {"lines": [{"spans": [
        {"font": "XpressChanak", "text": "Á", "size": 12, "bbox": [10, 10, 14, 22]},
        {"font": "XpressChanak", "text": "∑§", "size": 12, "bbox": [14, 10, 24, 22]},
    ]}]}
    before = deepcopy(block)
    assert decode_block(block) == "कि"
    assert block == before


def test_visible_gap_between_font_runs_keeps_words_separate():
    block = {"lines": [{"spans": [
        {"font": "XpressChanak", "text": "÷Ê⁄UÃ", "size": 12, "bbox": [10, 10, 40, 22]},
        {"font": "XpressChanak", "text": "∑§Ê", "size": 12, "bbox": [45, 10, 60, 22]},
    ]}]}
    assert decode_block(block) == "भारत का"


def test_line_geometry_keeps_paragraph_indent_and_baselines():
    block = GOLDEN[1]["block"]
    layout = structured_pdf.text_layout(block)
    assert len(layout["lines"]) == len(block["lines"])
    assert layout["lines"][0]["x"] > layout["lines"][1]["x"]
    for actual, original in zip(layout["lines"], block["lines"]):
        assert actual["width"] == original["bbox"][2] - original["bbox"][0]
        assert actual["y"] == original["spans"][0]["origin"][1] - layout["font_size"] * .9


def test_integrated_extraction_uses_golden_text_without_ocr(tmp_path, monkeypatch):
    path = tmp_path / "native.pdf"
    with fitz.open() as pdf:
        pdf.new_page(); pdf.save(path)
    monkeypatch.setattr(fitz.Page, "get_text", lambda *a, **k: {"blocks": [GOLDEN[1]["block"]]})
    monkeypatch.setattr(structured_pdf, "ocr_region", lambda *a, **k: pytest.fail("Supported digital Hindi must not use OCR"))
    page = structured_pdf.extract_page(path, 1)
    r = page["regions"][0]
    assert r["source"] == GOLDEN[1]["expected"]
    assert r["raw"] != r["source"]
    assert r["disposition"] == "translate"
    assert not r["source_approved"]
    assert r["alternatives"][0]["method"] == "xpress-chanak-v1"


def test_decoding_refresh_preserves_edits_and_fills_untouched_source(workspace):
    from copy import deepcopy
    store, identifier = workspace
    page = store.page(identifier, 1)
    fresh = deepcopy(page)
    for r in fresh["regions"]:
        r.update(source="भारत", disposition="translate", alternatives=[{"method": "xpress-chanak-v1", "text": "भारत"}])
    result = store.refresh_legacy_recognition(identifier, 1, page["revision"], fresh, "tester")
    assert all(r["source"] == "भारत" for r in result["regions"])
    assert not any(r["source_approved"] for r in result["regions"])
    assert store.refresh_legacy_recognition(identifier, 1, result["revision"], fresh, "tester") == result


@pytest.mark.skipif(not os.environ.get("HINDI_ACCEPTANCE_PDF"), reason="Set HINDI_ACCEPTANCE_PDF to the local supplied PDF")
def test_supplied_pdf_article_numbers_and_all_pages(monkeypatch):
    path = os.environ["HINDI_ACCEPTANCE_PDF"]
    monkeypatch.setattr(structured_pdf, "ocr_region", lambda *a, **k: pytest.fail("These supported pages need no OCR"))
    for number, phrases in [(1, ["भारत\nका\nसंविधान"]), (41, ["16.", "मूलवंश", "1956", "1995", "2001"]),
                            (101, ["124.", "उच्चतम न्यायालय", "2015", "1978", "1963"])]:
        page = structured_pdf.extract_page(path, number)
        text = "\n".join(r["source"] for r in page["regions"])
        assert all(phrase in text for phrase in phrases)
    total = failed = 0
    with fitz.open(path) as pdf:
        assert len(pdf) == 544
        for page in pdf:
            for b in page.get_text("dict")["blocks"]:
                if b["type"] == 0 and any("XpressChanak" in s["font"] for l in b["lines"] for s in l["spans"]):
                    total += 1
                    failed += decode_block(b) is None
    assert total == 4769
    assert failed <= 11  # Unmapped/ambiguous source glyphs deliberately retain OCR fallback.
