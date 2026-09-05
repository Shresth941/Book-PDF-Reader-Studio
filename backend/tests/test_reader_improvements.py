import asyncio
from pathlib import Path
from uuid import uuid4

from app.api.schemas import DefinitionRequest, TranslateRequest
from app.infrastructure.pdf_reader import PyMuPdfReader
from app.services.document_service import DocumentService
from app.services.export_service import ExportService
from app.services.language_service import DictionaryService, LibreTranslateService


def test_requests_do_not_have_the_old_text_length_limits():
    very_long_text = "अ" * 50_000
    assert TranslateRequest(text=very_long_text, source="auto", target="en").text == very_long_text
    assert DefinitionRequest(term=very_long_text, language="auto").term == very_long_text


def test_document_pages_are_cached_for_repeated_ranges():
    class FakeStorage:
        def path_for(self, identifier):
            return Path(f"{identifier}.pdf")

    class FakeReader:
        def __init__(self):
            self.calls = 0

        def extract(self, path, start, end):
            self.calls += 1
            return [{"page": page, "text": f"cached page {page}"} for page in range(start, end + 1)]

    reader = FakeReader()
    service = DocumentService(FakeStorage(), reader)
    document_id = uuid4()
    assert service.pages(document_id, 2, 1) == [
        {"page": 1, "text": "cached page 1"},
        {"page": 2, "text": "cached page 2"},
    ]
    assert service.pages(document_id, 1, 2) == [
        {"page": 1, "text": "cached page 1"},
        {"page": 2, "text": "cached page 2"},
    ]
    assert reader.calls == 1


def test_fragmented_text_on_a_full_page_scan_uses_ocr():
    class FakeRect:
        width = 100
        height = 100

    class FakePage:
        rect = FakeRect()

        def get_image_info(self):
            return [{"bbox": (0, 0, 100, 100)}]

    fragmented = "sity\nROWAN\neats\nta\nee\naa\nNO\neWay\nIk\nMM)\nLy\nDELHI UNIVERSITY"
    assert PyMuPdfReader._needs_scan_ocr(FakePage(), fragmented)


def test_hindi_translation_detects_the_source_and_uses_its_cache(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return [[["hello", "नमस्ते", None, None, None, None, None, []]]]

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, **kwargs):
            calls.append(params)
            return FakeResponse()

    monkeypatch.setattr("app.services.language_service.httpx.AsyncClient", FakeClient)
    service = LibreTranslateService(None, None)
    assert asyncio.run(service.translate("नमस्ते", "auto", "en")) == "hello"
    assert asyncio.run(service.translate("नमस्ते", "auto", "en")) == "hello"
    assert calls == [{"client": "gtx", "sl": "hi", "tl": "en", "dt": "t", "q": "नमस्ते"}]


def test_translation_caches_the_reverse_direction_for_a_round_trip(monkeypatch):
    calls = []

    async def fake_translate_chunk(client, text, source, target):
        calls.append((text, source, target))
        return "नमस्ते"

    service = LibreTranslateService(None, None)
    monkeypatch.setattr(service, "_translate_chunk", fake_translate_chunk)
    assert asyncio.run(service.translate("hello", "en", "hi")) == "नमस्ते"
    assert asyncio.run(service.translate("नमस्ते", "hi", "en")) == "hello"
    assert calls == [("hello", "en", "hi")]


def test_translation_chunks_stay_small_enough_for_unicode_query_strings():
    text = "यह एक परीक्षण वाक्य है। " * 100
    chunks = LibreTranslateService._chunks(text)
    assert "".join(chunks) == text
    assert all(len(chunk) <= 900 for chunk in chunks)


def test_hindi_terms_are_preserved_for_definition_searches():
    assert DictionaryService._sanitize_term("  नमस्ते, दुनिया!  ") == "नमस्ते, दुनिया"


def test_definition_search_normalizes_pdf_whitespace_and_hidden_marks():
    assert DictionaryService._sanitize_term("\u00a0कानून\u200b\n\tव्यवस्था!\u00a0") == "कानून व्यवस्था"


def test_definition_lookup_returns_english_and_hindi_for_a_hindi_query(monkeypatch):
    class FakeTranslator:
        async def translate(self, text, source, target):
            translations = {
                ("सुखाधिकार", "hi", "en"): "easement",
                ("A legal right over another person's land.", "en", "hi"): "किसी अन्य व्यक्ति की भूमि पर कानूनी अधिकार।",
            }
            return translations[(text, source, target)]

    service = DictionaryService(FakeTranslator())

    async def fake_english_definitions(client, term):
        assert term == "easement"
        return ["A legal right over another person's land."]

    async def no_hindi_wiktionary_result(*args, **kwargs):
        return []

    monkeypatch.setattr(service, "_english_definitions", fake_english_definitions)
    monkeypatch.setattr(service, "_wiktionary_definitions", no_hindi_wiktionary_result)
    assert asyncio.run(service.define("सुखाधिकार", "auto")) == [
        "English: easement — A legal right over another person's land for the beneficial enjoyment of one's own land.",
        "Hindi: सुखाचार — अपनी भूमि के लाभकारी उपभोग के लिए किसी अन्य व्यक्ति की भूमि पर प्राप्त विधिक अधिकार।",
    ]


def test_hindi_dictionary_meaning_is_shown_with_english_equivalent(monkeypatch):
    class FakeTranslator:
        async def translate(self, text, source, target):
            assert (text, source, target) == ("कानून व्यवस्था।", "hi", "en")
            return "A system of laws."

    service = DictionaryService(FakeTranslator())

    async def fake_hindi_wiktionary(client, term, language, allow_language_fallback=True):
        assert (term, language, allow_language_fallback) == ("कानून", "hi", False)
        return ["कानून व्यवस्था।"]

    monkeypatch.setattr(service, "_wiktionary_definitions", fake_hindi_wiktionary)
    assert asyncio.run(service.define("कानून", "auto")) == [
        "English: A system of laws.",
        "Hindi: कानून व्यवस्था।",
    ]


def test_legal_passage_returns_local_contextual_help():
    passage = (
        "Act XIII of 1898, the Burma Laws Act, repealing section 4 of Act XI of 1889, "
        "provides for the law to be administered by the Courts in Burma."
    )
    definitions = DictionaryService._passage_helpers(passage, "en")
    assert any(item.startswith("repealing —") for item in definitions)
    assert any(item.startswith("section —") for item in definitions)


def test_legal_translation_preserves_statutory_terms(monkeypatch):
    translated_chunks = []

    async def fake_translate_chunk(client, text, source, target):
        translated_chunks.append(text)
        return text

    service = LibreTranslateService(None, None)
    monkeypatch.setattr(service, "_translate_chunk", fake_translate_chunk)
    result = asyncio.run(service.translate(
        "An easement over the dominant heritage is a right of way.",
        "en", "hi", "Indian Easements Act, 1882",
    ))

    assert len(translated_chunks) == 1
    assert "PDFLEGALTERM" in translated_chunks[0]
    assert "easement" not in translated_chunks[0]
    assert "dominant heritage" not in translated_chunks[0]
    assert result == "An सुखाचार over the प्रधान सम्पदा is a मार्गाधिकार."


def test_legal_document_lookup_is_bilingual_and_does_not_need_a_network_call():
    class NoNetworkTranslator:
        async def translate(self, *args):
            raise AssertionError("a glossary lookup should not translate remotely")

    service = DictionaryService(NoNetworkTranslator())
    results = asyncio.run(service.define(
        "dominant heritage", "auto", "Section 4 of the Indian Easements Act explains the right.",
        "Indian Easements Act, 1882", "easements-pdf",
    ))
    assert results == [
        "English: dominant heritage — The land for whose beneficial enjoyment an easement exists.",
        "Hindi: प्रधान सम्पदा — वह प्रधान सम्पदा जिसके लाभकारी उपभोग के लिए सुखाचार विद्यमान है।",
    ]


def test_legal_passage_lookup_extracts_multiple_relevant_legal_terms():
    service = DictionaryService(None)
    results = asyncio.run(service.define(
        "Act XIII repeals section 4 and is administered by the Court.",
        "en", "The Burma Laws Act repeals section 4 for the Court.", "Burma Laws Act", "burma-pdf",
    ))
    assert any(item.startswith("English: act —") for item in results)
    assert any(item.startswith("Hindi: अधिनियम —") for item in results)


def test_export_service_generates_all_requested_download_types():
    exporter = ExportService()
    text = "Hello\nनमस्ते"
    txt, _, txt_extension = exporter.build(text, "txt")
    docx, _, docx_extension = exporter.build(text, "docx")
    pdf, _, pdf_extension = exporter.build(text, "pdf")
    assert txt.startswith(b"\xef\xbb\xbf") and txt_extension == "txt"
    assert docx.startswith(b"PK") and docx_extension == "docx"
    assert pdf.startswith(b"%PDF") and pdf_extension == "pdf"
