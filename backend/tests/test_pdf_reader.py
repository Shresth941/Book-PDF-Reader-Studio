import asyncio

import fitz
import pytest

from app.infrastructure.pdf_reader import PyMuPdfReader
from app.services.language_service import LibreTranslateService


def test_requested_page_is_extracted(tmp_path):
    path = tmp_path / "sample.pdf"; pdf = fitz.open()
    for text in ("First page", "Second page"):
        page = pdf.new_page(); page.insert_text((72, 72), text)
    pdf.save(path)
    assert PyMuPdfReader().extract(path, 2, 2) == [{"page": 2, "text": "Second page"}]


def test_reversed_range_is_normalized(tmp_path):
    path = tmp_path / "sample.pdf"; pdf = fitz.open()
    for text in ("First page", "Second page", "Third page"):
        page = pdf.new_page(); page.insert_text((72, 72), text)
    pdf.save(path)
    assert PyMuPdfReader().extract(path, 3, 1) == [
        {"page": 1, "text": "First page"},
        {"page": 2, "text": "Second page"},
        {"page": 3, "text": "Third page"},
    ]


def test_invalid_range_is_rejected(tmp_path):
    path = tmp_path / "sample.pdf"; pdf = fitz.open(); pdf.new_page(); pdf.save(path)
    with pytest.raises(ValueError): PyMuPdfReader().extract(path, 2, 2)


def test_pdf_bytes_can_be_read_without_persistent_disk():
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Serverless PDF")
    content = pdf.tobytes()

    reader = PyMuPdfReader()
    assert reader.page_count_bytes(content) == 1
    assert reader.extract_bytes(content, 1, 1) == [{"page": 1, "text": "Serverless PDF"}]


def test_legacy_hindi_font_is_omitted_but_english_spans_are_kept():
    class FakePage:
        def get_text(self, mode, sort=True):
            assert mode == "dict"
            return {
                "blocks": [
                    {
                        "lines": [
                            {
                                "spans": [
                                    {"font": "Shiva-Medium", "text": '?kks"k.kk i=k '},
                                    {"font": "MSTT31c61c", "text": "DECLARATION FORM"},
                                ]
                            },
                            {
                                "spans": [
                                    {"font": "Shiva-Medium", "text": "1-chek la[;k@"},
                                    {"font": "MSTT31c61c", "text": "Insurance No."},
                                ]
                            },
                        ]
                    }
                ]
            }

    assert PyMuPdfReader()._extract_ordered_text(FakePage()) == "DECLARATION FORM\n1-Insurance No."


def test_windows_encoded_punctuation_is_made_readable():
    assert PyMuPdfReader()._clean_text("\x93Family\x94") == '"Family"'


def test_ocr_cleanup_repairs_wrapped_words_and_ocr_separator_artifacts():
    assert PyMuPdfReader._clean_ocr_lines([
        "Compensation for damage caused by extin-",
        "guishment or suspension",
        "servient heritages respectivelyThey_must_be_quite",
    ]) == "\n".join([
        "Compensation for damage caused by extinguishment or suspension",
        "servient heritages respectively They must be quite",
    ])


def test_contents_detection_does_not_treat_normal_prose_as_an_index():
    contents_lines = [
        "43. First entry", "109", "44. Second entry", "112",
        "45. Third entry", "113", "46. Fourth entry", "114",
    ]
    prose_lines = [
        "An easement exists for the beneficial enjoyment of land.",
        "The owner may exercise the right over a neighbouring property.",
    ]
    assert PyMuPdfReader._is_contents_style(contents_lines)
    assert not PyMuPdfReader._is_contents_style(prose_lines)


def test_rapid_ocr_discards_isolated_lowercase_scan_noise(monkeypatch):
    class FakeOutput:
        txts = ("A valid line", "y", "ii")
        scores = (0.99, 0.72, 0.99)

    class FakeEngine:
        def __call__(self, image):
            return FakeOutput()

    class FakePage:
        def get_pixmap(self, dpi, alpha):
            class FakePixmap:
                def tobytes(self, format):
                    return b"not-an-image"
            return FakePixmap()

    reader = PyMuPdfReader()
    monkeypatch.setattr("app.infrastructure.pdf_reader._rapid_ocr_engine", FakeEngine())
    monkeypatch.setattr("app.infrastructure.pdf_reader._rapid_ocr_failed", False)
    monkeypatch.setattr("app.infrastructure.pdf_reader.Image.open", lambda _: object())
    assert reader._rapid_ocr_page(FakePage()) == "A valid line\nii"


def test_positioned_ocr_rejoins_contents_rows_and_folio_spacing():
    data = {
        "text": [], "conf": [], "block_num": [], "par_num": [], "line_num": [],
        "left": [], "top": [], "width": [], "height": [],
    }

    def add_segment(text, left, top, block, confidence="91"):
        for offset, word in enumerate(text.split()):
            data["text"].append(word)
            data["conf"].append(confidence)
            data["block_num"].append(block)
            data["par_num"].append(1)
            data["line_num"].append(1)
            data["left"].append(left + offset * 24)
            data["top"].append(top)
            data["width"].append(18)
            data["height"].append(20)

    # Tesseract's sparse-mode layout identifies these pieces as different
    # blocks, despite their matching visual rows in the source image.
    add_segment("(", 290, 20, 1)
    add_segment("vii", 320, 22, 2)
    add_segment(")", 360, 20, 3)
    add_segment("PAGE", 470, 58, 4)
    add_segment("43", 115, 98, 5)
    add_segment("Extinction by permanent change in dominant", 160, 98, 6)
    add_segment("heritage", 160, 130, 7)
    add_segment("109", 510, 130, 8)
    add_segment("44", 115, 162, 9)
    add_segment("Extinction on permanent alteration of servient", 160, 162, 10)
    add_segment("heritage by superior force", 160, 194, 11)
    add_segment("It2", 510, 194, 12, confidence="16")

    assert PyMuPdfReader._rebuild_positioned_ocr_lines(data) == "\n".join([
        "( vii )",
        "PAGE",
        "43. Extinction by permanent change in dominant",
        "heritage 109",
        "44. Extinction on permanent alteration of servient",
        "heritage by superior force 112",
    ])


def test_positioned_ocr_keeps_unrelated_full_text_columns_separate():
    data = {
        "text": ["Left", "column", "prose", "Right", "column", "prose"],
        "conf": ["90"] * 6,
        "block_num": [1, 1, 1, 2, 2, 2],
        "par_num": [1] * 6,
        "line_num": [1] * 6,
        "left": [90, 120, 165, 470, 510, 555],
        "top": [80] * 6,
        "width": [20] * 6,
        "height": [20] * 6,
    }

    assert PyMuPdfReader._rebuild_positioned_ocr_lines(data) == "Left column prose\nRight column prose"


def test_positioned_ocr_preserves_roman_contents_labels_and_page_references():
    data = {
        "text": [], "conf": [], "block_num": [], "par_num": [], "line_num": [],
        "left": [], "top": [], "width": [], "height": [],
    }

    def add_segment(text, left, top, block, confidence="91"):
        for offset, word in enumerate(text.split()):
            data["text"].append(word)
            data["conf"].append(confidence)
            data["block_num"].append(block)
            data["par_num"].append(1)
            data["line_num"].append(1)
            data["left"].append(left + offset * 24)
            data["top"].append(top)
            data["width"].append(18)
            data["height"].append(20)

    add_segment("PAGE", 470, 20, 1)
    add_segment("I", 115, 60, 2)
    add_segment("Preface", 160, 60, 3)
    add_segment("iv", 510, 60, 4, confidence="16")
    add_segment("II", 115, 94, 5)
    add_segment("Introduction", 160, 94, 6)
    add_segment("vii", 510, 94, 7, confidence="16")

    assert PyMuPdfReader._rebuild_positioned_ocr_lines(data) == "\n".join([
        "PAGE",
        "I. Preface iv",
        "II. Introduction vii",
    ])


def test_contents_numbering_repairs_only_a_verified_consecutive_ocr_run():
    lines = [
        "PAGE",
        "43. First entry", "44. Second entry", "45. Third entry", "46. Fourth entry",
        "47. Fifth entry", "48. Sixth entry", "49. Seventh entry",
        "590. Eighth entry", "51. Ninth entry",
        "CHAPTER VI", "52. Tenth entry", "53. Eleventh entry", "54. Twelfth entry",
        "55. Thirteenth entry", "86. Fourteenth entry", "5g Fifteenth entry",
    ]

    repaired = PyMuPdfReader._repair_contents_numbering(lines)

    assert repaired[8] == "50. Eighth entry"
    assert repaired[15] == "56. Fourteenth entry"
    assert repaired[16] == "57. Fifteenth entry"


def test_translation_falls_back_to_public_service(monkeypatch):
    class FakeResponse:
        def __init__(self, payload): self._payload = payload
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, **kwargs):
            self.calls.append(("POST", url, json))
            return FakeResponse({"translatedText": "नमस्ते"})

        async def get(self, url, params=None, **kwargs):
            self.calls.append(("GET", url, params))
            # Google Translate response format
            return FakeResponse([[[" नमस्ते", "hello", None, None, None, None, None, [[]]]]])

    monkeypatch.setattr("app.services.language_service.httpx.AsyncClient", FakeAsyncClient)
    service = LibreTranslateService(None, None)

    async def run():
        return await service.translate("hello", "en", "hi")

    assert asyncio.run(run()) == "नमस्ते"
