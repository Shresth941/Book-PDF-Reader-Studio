import os
import pytest
from app.services import local_translation
from app.services.contents_translation import parse_rows, is_contents
from app.core.review import ReviewError


def region(source):
    return {"id": "contents", "source": source, "source_language": "hi", "bbox": [70, 100, 500, 700]}


def test_wrapped_titles_and_page_numbers_are_separate():
    rows = parse_rows("143. उच्चतम न्यायालय से परामर्श करने की राष्ट्रपति की शक्ति ....\n80\n144. सिविल और न्यायिक प्राधिकारियों द्वारा उच्चतम न्यायालय की सहायता में\nकार्य किया जाना .... 81\nअध्याय 5—भारत का नियंत्रक-महालेखापरीक्षक")
    assert [(r["label"], r["page"]) for r in rows] == [("143.", "80"), ("144.", "81"), ("", "")]
    assert rows[1]["source"].endswith("कार्य किया जाना")
    assert rows[2]["kind"] == "heading"


def test_every_unknown_entry_gets_its_own_model_unit(monkeypatch):
    seen = []
    def model(root, regions, target):
        seen.extend(r["source"] for r in regions)
        return {"regions": [{"id": r["id"], "translation": "Translated title"} for r in regions]}
    monkeypatch.setattr(local_translation, "_translate_segments", model)
    output = local_translation.translate_segments("unused", [region("1. पहला विषय .... 7\n2. दूसरा विषय ....\n8")], "en")
    assert seen == ["पहला विषय", "दूसरा विषय"]
    assert output["regions"][0]["translation"] == "1. Translated title .... 7\n2. Translated title .... 8"


def test_legal_headings_do_not_need_a_model(monkeypatch):
    monkeypatch.setattr(local_translation, "_translate_segments", lambda *args: pytest.fail("Exact headings must stay local"))
    result = local_translation.translate_segments("unused", [region("148. भारत का नियंत्रक-महालेखापरीक्षक .... 84\n149. नियंत्रक-महालेखापरीक्षक के कर्तव्य और शक्तियां .... 85")], "en", domain="legal")["regions"][0]
    assert "Comptroller and Auditor-General of India" in result["translation"]
    assert len(result["translation_layout"]["rows"]) == 2
    assert not result["translation_approved"] and not result["quality_qualified"]


def test_malformed_contents_does_not_silently_flatten():
    with pytest.raises(ReviewError, match="contents entry"):
        local_translation.translate_segments("unused", [region("1. विषय .... 7\n2. दूसरा ....\nअध्याय")], "en")
    assert not is_contents([region("Some prose ... with ellipses ...")])


@pytest.mark.skipif(not os.getenv("HINDI_ACCEPTANCE_PDF"), reason="Local private acceptance PDF not supplied")
def test_supplied_page_16_preserves_all_23_entries(monkeypatch):
    from app.infrastructure.structured_pdf import extract_page
    page = extract_page(os.environ["HINDI_ACCEPTANCE_PDF"], 16)
    for r in page["regions"]:
        r["source_language"] = "hi"
    monkeypatch.setattr(local_translation, "_translate_segments", lambda *args: pytest.fail("Page 16 headings have exact equivalents"))
    result = local_translation.translate_segments("unused", page["regions"], "en", domain="legal")
    rows = [row for r in result["regions"] for row in r["translation_layout"]["rows"] if row["kind"] == "entry"]
    assert len(rows) == 23
    assert [r["label"] for r in rows] == ["143.", "144.", "144A."] + [f"{n}." for n in range(145, 165)]
    assert [r["page"] for r in rows] == ["80", "81", "81", "81", "83", "84", "84", "85", "85", "86", "87", "87", "87", "87", "88", "88", "88", "89", "89", "89", "89", "90", "90"]
