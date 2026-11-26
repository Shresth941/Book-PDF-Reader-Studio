import hashlib
from pathlib import Path

import fitz
from app.services import local_translation
from app.services.constitution_reference import HINDI_SHA, contents_title, index, reading_location


def test_matching_published_pdf_and_edition():
    pdf = Path(__file__).resolve().parents[2] / "frontend/public/references/constitution-2015-en.pdf"
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == "5bb100f186eda2e9366f87f74c1b2b1507cf245355ad8886848f9f0c7f221966"
    with fitz.open(pdf) as edition:
        assert len(edition) == 494 and "9th November, 2015" in edition[2].get_text()
        assert reading_location(41)["page"] == 35
        assert reading_location(16)["page"] == 15
        assert "16. (1)" in edition[34].get_text()
        assert all(1 <= reading_location(n)["page"] <= len(edition) for n in range(1, 545))


def test_source_guard_including_article_suffixes():
    repeated = contents_title("144क.", "[निरसित।]")
    assert repeated[0] == "144A" and repeated[1]["en"] == "[Repealed.]"
    assert contents_title("143.", "[निरसित।]") is None
    assert contents_title("144क.", "[संशोधित।]") is None


def test_verified_clause_only_used_for_exact_page_and_edition(monkeypatch):
    clause = next(k for k in index("passages") if k.startswith("16. (1)"))
    def model(root, regions, target):
        return {"regions": [{"id": r["id"], "translation": "local model draft"} for r in regions]}
    monkeypatch.setattr(local_translation, "_translate_segments", model)
    def translate(source, digest, page):
        return local_translation.translate_segments(".", [{"id": "r", "source": source, "source_language": "hi"}],
            "en", "legal", document_sha=digest, page_number=page)["regions"][0]
    result = translate(clause, HINDI_SHA, 41)
    assert result["translation"].startswith("16. (1) There shall be equality of opportunity")
    assert result["reference_page"] == 35 and not result["translation_approved"]
    for args in [(clause, "different edition", 41), (clause, HINDI_SHA, 40), (clause+" नहीं", HINDI_SHA, 41)]:
        assert translate(*args)["translation"] == "local model draft"
