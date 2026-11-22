"""Opt-in real OCR smoke on generated scans, never a recognition-quality benchmark."""
import os
from pathlib import Path

import fitz
import pytest

from app.core.config import Settings
from app.infrastructure.bounded_worker import BoundedWorker
from app.infrastructure.structured_pdf import tesseract_path
from app.services.review_export import shaped_document, checked_pdf_output

pytestmark = pytest.mark.skipif(os.getenv("RUN_LOCAL_OCR_TESTS") != "1", reason="Real OCR smoke requires the local Tesseract executable and Hindi/English data.")


@pytest.mark.parametrize("text,expected", [("This is a scanned document.", "scanned"), ("यह एक पुस्तक है।", "पुस्तक")])
def test_scanned_page_produces_unapproved_text(tmp_path, text, expected):
    assert tesseract_path(), "Install Tesseract before running the real OCR smoke."
    models = Path(__file__).resolve().parents[1] / "models"
    assert all((models / "tessdata" / f"{language}.traineddata").is_file() for language in ("eng", "hin"))
    authored = shaped_document(tmp_path)
    authored.add_page()
    authored.set_font("Review", size=22)
    authored.multi_cell(0, 36, text)
    with fitz.open(stream=checked_pdf_output(authored), filetype="pdf") as source, fitz.open() as scan:
        pix = source[0].get_pixmap(dpi=180)
        p = scan.new_page(width=source[0].rect.width, height=source[0].rect.height)
        p.insert_image(p.rect, stream=pix.tobytes("png"))
        assert not p.get_text().strip(), "Fixture must be image-only, with no native text."
        path = tmp_path / "scan.pdf"
        scan.save(path)
    settings = Settings(data_dir=tmp_path / "workspace", models_dir=models)
    result = BoundedWorker(settings).run("extract", {"path": str(path), "number": 1})
    # OCR is evidence until a person explicitly adopts it as editable source text.
    assert expected in " ".join(a["text"] for r in result["regions"] for a in r["alternatives"])
    assert all(not r["source"] for r in result["regions"])
    assert all(not r["source_approved"] for r in result["regions"])
