from pathlib import Path
import fitz
import pytest
from app.core.review import ReviewError
from app.services.compatible_pdf import compatible_text_pdf, verify_appearance
from app.services.review_export import shaped_document


@pytest.mark.parametrize("mutation", ["removed", "added", "color", "dimensions"])
def test_visible_mutations_cannot_pass_compatibility_check(mutation):
    source, result = fitz.open(), fitz.open()
    a = source.new_page(width=300, height=300)
    b = result.new_page(width=301 if mutation == "dimensions" else 300, height=300)
    a.draw_rect(fitz.Rect(30, 30, 170, 70), color=(0, 0, 1), fill=(0, 0, 1))
    if mutation != "removed":
        color = (1, 0, 0) if mutation == "color" else (0, 0, 1)
        b.draw_rect(fitz.Rect(30, 30, 170, 70), color=color, fill=color)
    if mutation == "added":
        b.insert_text((50, 150), "An invented clause")
    with pytest.raises(ReviewError):
        verify_appearance(a, b)
    source.close(); result.close()


def test_verified_hindi_layer_keeps_graphics_and_exact_logical_lines(tmp_path):
    text = "किराया, विधि, कृपया, क्षति, न्यायालय, ऋणी, छोड़ें और रुपये 100.50। सत्यापित"
    original = shaped_document(tmp_path)
    original.add_page()
    original.set_fill_color(200, 230, 245)
    original.rect(35, 35, 520, 110, style="F")
    original.set_xy(50, 60)
    original.multi_cell(475, 22, text=text, new_x="LEFT", new_y="NEXT")
    raw = bytes(original.output())
    converted = compatible_text_pdf(raw, lambda size: shaped_document(tmp_path, size))
    (tmp_path / "compatible-hindi.pdf").write_bytes(converted)
    with fitz.open(stream=raw, filetype="pdf") as before, fitz.open(stream=converted, filetype="pdf") as after:
        assert before[0].get_text().split() == after[0].get_text().split()
        assert "सत्यापित" in after[0].get_text()
        assert "&" not in after[0].get_text() and "\x00" not in after[0].get_text()
        assert len(after[0].get_drawings()) > 1
        assert not after[0].get_images(), "Glyphs remain vector outlines, not raster images"
        verify_appearance(before[0], after[0])


def test_injected_wrong_logical_text_cannot_pass(tmp_path):
    source = shaped_document(tmp_path)
    source.add_page()
    source.multi_cell(450, 20, text="सत्यापित पाठ")
    def corrupt_factory(size):
        pdf = shaped_document(tmp_path, size)
        original_text = pdf.text
        pdf.text = lambda x, y, text: original_text(x, y, "Invented replacement")
        return pdf
    with pytest.raises(ReviewError, match="logical text layer changed"):
        compatible_text_pdf(bytes(source.output()), corrupt_factory)
