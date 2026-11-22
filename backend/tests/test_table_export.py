"""Table export preserves cell relationships, spans, visual cells and readable fit."""
from copy import deepcopy
import tempfile

from docx import Document
import fitz
import pytest

from app.core.review import ReviewError
from app.core.structure_review import decide_table
from app.infrastructure.structured_pdf import extract_page
from app.services.review_export import (
    build_export, canonical_text, checked_pdf_output, region_text,
    shaped_document, verify_pdf_content,
)
from app.services.table_export import content_units, render_pdf_table


def native_table_snapshot(tmp_path, merged=False):
    """Known native text is the target text; recognition/approval rules have separate tests."""
    original = tmp_path / ("merged-source.pdf" if merged else "table-source.pdf")
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((50, 35), "Before the table")
        if merged:
            page.draw_rect((50, 80, 350, 230))
            page.draw_line((50, 130), (350, 130))
            page.draw_line((200, 130), (200, 230))
            page.draw_line((200, 180), (350, 180))
            for point, value in (((60, 105), "Combined heading"), ((60, 155), "Alice"),
                                 ((210, 155), "100"), ((210, 205), "200")):
                page.insert_text(point, value)
        else:
            for x in (50, 150, 250):
                page.draw_line((x, 80), (x, 180))
            for y in (80, 130, 180):
                page.draw_line((50, y), (250, y))
            for point, value in (((60, 105), "Name"), ((160, 105), "Amount"), ((60, 155), "Alice")):
                page.insert_text(point, value)
        page.insert_text((50, 280), "After the table")
        document.save(original)
    page = extract_page(str(original), 1)
    table = page["table_proposals"]["tables"][0]
    assert table["status"] == "proposed", table["warnings"]
    decide_table(page, table["id"], "adopt", "Known synthetic native grid checked against source.", "Test reviewer")
    for item in page["regions"]:
        if item["source"]:
            item.update(disposition="already_target", source_language="en")
        item["source_approved"] = True
    decide_table(page, table["id"], "approve_structure", "Known cell positions and spans checked against source.", "Test reviewer")
    page["coverage_approved"] = True
    return {"document": {"filename": "Native table fixture", "revision": 1, "page_count": 1,
                         "target": "en", "metadata": {}},
            "pages": [page], "glossary": [], "blockers": []}, original


def members(snapshot):
    return [r for r in snapshot["pages"][0]["regions"] if r.get("table_id")]


def mapped_by_text(result):
    return {entry["expected"]: entry for entry in result["content_map"]}


def table_border(span):
    return fitz.Rect(span["bbox"]) + (-3, -3, 3, 3)


def close_rect(a, b, tolerance=0.05):
    return max(abs(x - y) for x, y in zip(a, b)) <= tolerance


def test_readable_pdf_preserves_cell_locations_and_draws_the_blank_cell(tmp_path):
    snapshot, original = native_table_snapshot(tmp_path)
    output = tmp_path / "cells.pdf"
    result = build_export(snapshot, str(original), str(output), "pdf", "readable")
    assert result["text_verified"] and result["render_verified"]
    mapped = mapped_by_text(result)
    assert set(mapped) == {"Before the table", "Name", "Amount", "Alice", "After the table"}
    name, amount, alice = [mapped[text]["spans"][0] for text in ("Name", "Amount", "Alice")]
    assert name["page"] == amount["page"] == alice["page"]
    assert name["bbox"][0] < amount["bbox"][0] and name["bbox"][1] == amount["bbox"][1]
    assert alice["bbox"][0] == name["bbox"][0] and alice["bbox"][1] > name["bbox"][3]
    blank_border = fitz.Rect(amount["bbox"][0] - 3, alice["bbox"][1] - 3,
                             amount["bbox"][2] + 3, alice["bbox"][3] + 3)
    with fitz.open(output) as pdf:
        page = pdf[name["page"] - 1]
        rectangles = [item[1] for path in page.get_drawings() for item in path["items"] if item[0] == "re"]
        assert any(close_rect(rect, blank_border) for rect in rectangles), "The blank cell must retain a visible border."
        assert page.get_text(clip=blank_border + (4, 4, -4, -4)).strip() == ""
        for text in ("Name", "Amount", "Alice"):
            assert canonical_text(page.get_text(clip=fitz.Rect(mapped[text]["spans"][0]["bbox"]))) == text
    assert len(members(snapshot)) == 4 and members(snapshot)[-1]["source"] == ""


def test_merged_spans_survive_pdf_docx_and_explicit_text_markers(tmp_path):
    snapshot, original = native_table_snapshot(tmp_path, merged=True)
    pdf_path = tmp_path / "merged.pdf"
    result = build_export(snapshot, str(original), str(pdf_path), "pdf", "readable")
    mapped = mapped_by_text(result)
    heading, person, first, second = [mapped[text]["spans"][0] for text in ("Combined heading", "Alice", "100", "200")]
    assert person["page"] == first["page"] == second["page"]
    assert close_rect(table_border(heading), fitz.Rect(table_border(person).x0, table_border(heading).y0,
                                                      table_border(first).x1, table_border(heading).y1))
    assert table_border(person).y0 == pytest.approx(table_border(first).y0)
    assert table_border(person).y1 == pytest.approx(table_border(second).y1)
    assert table_border(person).height == pytest.approx(table_border(first).height + table_border(second).height)
    with fitz.open(pdf_path) as pdf:
        text = "\n".join(p.get_text() for p in pdf)
        assert text.count("Combined heading") == text.count("Alice") == 1

    docx_path = tmp_path / "merged.docx"
    build_export(snapshot, str(original), str(docx_path), "docx", "readable")
    table = Document(docx_path).tables[0]
    assert len(table.rows) == 3 and len(table.columns) == 2
    assert table.cell(0, 0)._tc is table.cell(0, 1)._tc
    assert table.cell(1, 0)._tc is table.cell(2, 0)._tc
    assert table.cell(0, 0).text == "Combined heading"
    assert table.cell(1, 0).text == "Alice"
    assert [table.cell(row, 1).text for row in (1, 2)] == ["100", "200"]

    text_path = tmp_path / "merged.txt"
    build_export(snapshot, str(original), str(text_path), "txt", "readable")
    text = text_path.read_text(encoding="utf-8-sig")
    assert "[Table: 3 rows x 2 columns]" in text
    assert "Row 1, column 1, span 1 x 2: Combined heading" in text
    assert "Row 2, column 1, span 2 x 1: Alice" in text
    assert "Row 2, column 2, span 1 x 1: 100" in text
    assert "Row 3, column 2, span 1 x 1: 200" in text


def test_multiline_hindi_cell_does_not_overlap_following_rows_or_paragraph(tmp_path):
    snapshot, original = native_table_snapshot(tmp_path)
    item = next(r for r in members(snapshot) if r["source"] == "Alice")
    item.update(disposition="translate", translation="ऋणी को निर्धारित समय पर भुगतान करना होगा। " * 7)
    output = tmp_path / "hindi-cell.pdf"
    result = build_export(snapshot, str(original), str(output), "pdf", "readable")
    mapped = mapped_by_text(result)
    cell_span = mapped[item["translation"]]["spans"][0]
    after = mapped["After the table"]["spans"][0]
    assert cell_span["bbox"][3] - cell_span["bbox"][1] > 40
    assert after["page"] > cell_span["page"] or after["bbox"][1] > cell_span["bbox"][3]
    with fitz.open(output) as pdf:
        rendered = pdf[cell_span["page"] - 1]
        actual = rendered.get_text(clip=fitz.Rect(cell_span["bbox"]))
        assert canonical_text(actual) == canonical_text(item["translation"])
        assert actual.count("ऋणी") == 7
        assert "After the table" not in actual
        rendered.get_pixmap().save(tmp_path / "hindi-table-preview.png")


def test_pagination_keeps_vertical_merged_group_on_one_page(tmp_path):
    snapshot, original = native_table_snapshot(tmp_path, merged=True)
    unit = next(unit for unit in content_units(snapshot["pages"][0]) if unit["type"] == "table")
    output = tmp_path / "page-break-table.pdf"
    with fitz.open(original) as source, tempfile.TemporaryDirectory() as font_dir:
        pdf = shaped_document(font_dir)
        pdf.add_page()
        # Only the first nonspanning header row fits; both rows sharing Alice
        # must move together to the following page.
        mapped = render_pdf_table(pdf, {**unit, "start_y": pdf.h - pdf.b_margin - 30}, 1, region_text, source)
        checked_pdf_output(pdf, output)
    mapping = {item["expected"]: item["spans"] for item in mapped}
    assert mapping["Combined heading"][0]["page"] == 1
    assert all(len(mapping[text]) == 1 and mapping[text][0]["page"] == 2 for text in ("Alice", "100", "200"))
    with fitz.open(output) as pdf:
        assert pdf.page_count == 2
        verify_pdf_content(pdf, mapped)


def test_group_taller_than_a_page_fails_explicitly_without_a_clipped_pdf(tmp_path):
    snapshot, original = native_table_snapshot(tmp_path, merged=True)
    item = next(r for r in members(snapshot) if r["source"] == "Alice")
    item.update(disposition="translate", translation=("A required line in this merged cell.\n" * 70).rstrip())
    output = tmp_path / "overflow-table.pdf"
    with pytest.raises(ReviewError) as error:
        build_export(snapshot, str(original), str(output), "pdf", "readable")
    assert error.value.code == "table_row_overflow"
    assert not output.exists()


def test_preserved_table_cell_embeds_its_source_crop_in_pdf_and_docx(tmp_path):
    snapshot, original = native_table_snapshot(tmp_path)
    preserved = next(r for r in members(snapshot) if r["source"] == "Alice")
    preserved.update(disposition="preserve", contains_text=False, reason="Known visual source crop for renderer fixture.")
    pdf_path = tmp_path / "visual-cell.pdf"
    result = build_export(snapshot, str(original), str(pdf_path), "pdf", "readable")
    assert preserved["id"] not in {entry["region_id"] for entry in result["content_map"]}
    with fitz.open(pdf_path) as pdf:
        image_rects = [image["bbox"] for page in pdf for image in page.get_image_info()]
        assert len(image_rects) == 1
        page = next(page for page in pdf if page.get_image_info())
        embedded = fitz.Pixmap(pdf, page.get_images()[0][0])
        with fitz.open(original) as source:
            box = fitz.Rect(preserved["bbox"])
            scale = min(2, 1200 / max(box.width, box.height))
            expected = source[0].get_pixmap(clip=box, matrix=fitz.Matrix(scale, scale), alpha=False)
        assert (embedded.width, embedded.height, embedded.n) == (expected.width, expected.height, expected.n)
        assert embedded.samples == expected.samples, "The embedded image must retain the exact source crop, including its content."
        assert "Alice" not in page.get_text(), "The visual crop must not also be rendered as duplicate text."
    docx_path = tmp_path / "visual-cell.docx"
    build_export(snapshot, str(original), str(docx_path), "docx", "readable")
    document = Document(docx_path)
    assert len(document.inline_shapes) == 1
    assert document.tables[0].cell(1, 0)._tc.xpath(".//w:drawing")
    assert document.tables[0].cell(1, 1).text == ""


@pytest.mark.parametrize("fault", ["interleaved", "reversed", "missing"])
def test_invalid_cell_order_refuses_export_before_writing_any_output(tmp_path, fault):
    snapshot, original = native_table_snapshot(tmp_path)
    snapshot = deepcopy(snapshot)
    page = snapshot["pages"][0]
    if fault == "interleaved":
        page["regions"].insert(2, page["regions"].pop(-1))
    elif fault == "reversed":
        page["regions"][1], page["regions"][2] = page["regions"][2], page["regions"][1]
    else:
        page["regions"] = [r for r in page["regions"] if not (r.get("table_id") and not r["source"])]
    for index, item in enumerate(page["regions"]):
        item["order"] = index
    output = tmp_path / "invalid-table.txt"
    with pytest.raises(ReviewError) as error:
        build_export(snapshot, str(original), str(output), "txt", "readable")
    assert error.value.code == "table_export_order"
    assert not output.exists()
