from pathlib import Path
import pytest
from app.services import local_translation
from app.services.legal_translation import exact_legal_text, legal_glossary, correct_known_terms
from test_review_workflow import workspace, source_pdf


@pytest.mark.parametrize("source,expected", [
    ("भारत का संविधान\n(भाग 3—मूल अधिकार)", "Constitution of India\n(Part 3 — Fundamental Rights)"),
    ("भारत\nका\nसंविधान", "Constitution of India"),
    ("उच्चतम न्यायालय", "Supreme Court"),
    ("(भाग ३—मूल अधिकार)", "(Part ३ — Fundamental Rights)"),
    ("लोक नियोजन के विषय में अवसर की समता", "Equality of opportunity in matters of public employment"),
])
def test_exact_legal_headings(source, expected):
    assert exact_legal_text(source, "en", legal_glossary("en")) == expected


def test_no_fuzzy_legal_reconstruction():
    assert exact_legal_text("मूल अधिकार नहीं दिए जाएंगे।", "en", legal_glossary("en")) is None


def test_reverse_terms_have_one_preferred_sense():
    entries = legal_glossary("hi")
    assert len({e["source"].casefold() for e in entries}) == len(entries)
    assert exact_legal_text("Fundamental Rights", "hi", entries) == "मूल अधिकार"


def test_only_source_bound_mistranslations_are_changed():
    value, changes = correct_known_terms("मूल अधिकार", "Root Authority", "en", legal_glossary("en"))
    assert value == "Fundamental Rights" and changes
    assert correct_known_terms("जड़", "root authority", "en", legal_glossary("en"))[0] == "root authority"
    assert correct_known_terms("मूल अधिकार", "The State may act; it will not act.", "en", legal_glossary("en"))[0] == "The State may act; it will not act."


def test_legal_heading_bypasses_general_model_and_preserves_order(monkeypatch):
    calls = []
    def model(root, regions, target):
        calls.append(regions)
        return {"regions": [{"id": r["id"], "translation": "Root Authority must be respected."} for r in regions]}
    monkeypatch.setattr(local_translation, "_translate_segments", model)
    regions = [{"id": "heading", "source": "मूल अधिकार", "source_language": "hi"},
               {"id": "body", "source": "मूल अधिकार का सम्मान किया जाना चाहिए।", "source_language": "hi"}]
    result = local_translation.translate_segments(Path("unused"), regions, "en", domain="legal")
    assert [r["id"] for r in result["regions"]] == ["heading", "body"]
    assert calls[0] == [regions[1]]
    assert result["regions"][0]["translation"] == "Fundamental Rights"
    assert result["regions"][1]["translation"] == "Fundamental Rights must be respected."
    assert all(not r["translation_approved"] and not r["quality_qualified"] for r in result["regions"])


def test_document_glossary_overrides_builtin_heading(monkeypatch):
    monkeypatch.setattr(local_translation, "_translate_segments", lambda *a: pytest.fail("Exact glossary entry should win"))
    result = local_translation.translate_segments("unused", [{"id": "a", "source": "मूल अधिकार", "source_language": "hi"}], "en", domain="legal",
        glossary=[{"source": "मूल अधिकार", "target": "Fundamental rights (defined term)", "direction": "hi-en"}])
    assert result["regions"][0]["translation"] == "Fundamental rights (defined term)"


def test_general_mode_does_not_rewrite_model_prose(monkeypatch):
    output = {"regions": [{"id": "a", "translation": "Root Authority"}]}
    monkeypatch.setattr(local_translation, "_translate_segments", lambda *a: output)
    assert local_translation.translate_segments("unused", [{"id": "a", "source": "मूल अधिकार", "source_language": "hi"}], "en") == output


def test_legal_context_enables_missing_term_review(workspace):
    store, identifier = workspace
    doc = store.document(identifier)
    store.update_context(identifier, doc["revision"], {"target": "en", "domain": "legal", "jurisdiction": "India"})
    from app.core.review import translation_issues
    assert any(i["code"].startswith("glossary:") for i in translation_issues("मूल अधिकार", "Root Authority", store.glossary(identifier)))


def test_punctuation_span_does_not_remove_heading_italics():
    from app.infrastructure.structured_pdf import text_layout
    block = {"bbox": [10, 10, 110, 30], "lines": [{"bbox": [10, 10, 110, 30], "spans": [
        {"text": "भारत का संविधान", "font": "XpressChanakItalic", "size": 12, "origin": [10, 25]},
        {"text": "—", "font": "Palatino", "size": 12, "origin": [100, 25]},
    ]}]}
    assert text_layout(block)["italic"] is True
