"""Real ruled-table fixtures and injected failures, without OCR/model loading."""
import json

import fitz
import pytest

from app.infrastructure.table_structure import extract_table_proposals


@pytest.fixture
def page():
    with fitz.open() as document:
        yield document.new_page(width=600, height=800)


def draw_grid(page, xs=(50, 150, 250), ys=(50, 100, 150)):
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))


def cell_at(table, row, column):
    return next(cell for cell in table["cells"] if (cell["row"], cell["column"]) == (row, column))


def test_native_values_and_blank_cells_retain_exact_alignment(page):
    draw_grid(page)
    page.insert_text((60, 75), "Person")
    page.insert_text((160, 75), "Amount")
    page.insert_text((60, 125), "Alice")
    text_before = page.get_text()
    result = extract_table_proposals(page)
    assert result["status"] == "proposed"
    assert not result["warnings"]
    table = result["tables"][0]
    assert table["status"] == "proposed", table["warnings"]
    assert (table["row_count"], table["column_count"]) == (2, 2)
    assert table["bbox"] == [50, 50, 250, 150]
    assert [c["raw"] for c in table["cells"]] == ["Person", "Amount", "Alice", ""]
    assert [(c["row"], c["column"]) for c in table["cells"]] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    blank = cell_at(table, 1, 1)
    assert blank["is_blank"] is True and blank["native_text"] == ""
    assert blank["bbox"] == [150, 100, 250, 150]
    assert all(c["row_span"] == c["column_span"] == 1 for c in table["cells"])
    assert all(table["grid"][c["row"]][c["column"]] == c["id"] for c in table["cells"])
    assert table["confidence"] is None and table["review_required"] is True
    assert table["header"]["names"] == ["Person", "Amount"]
    assert table["header"]["review_required"] is True
    assert page.get_text() == text_before
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    assert extract_table_proposals(page) == result


def test_merged_header_spans_columns_without_duplicating_text(page):
    page.draw_rect((50, 50, 250, 200))
    for y in (100, 150):
        page.draw_line((50, y), (250, y))
    page.draw_line((150, 100), (150, 200))
    page.insert_text((60, 75), "Combined heading")
    page.insert_text((60, 125), "Name")
    page.insert_text((160, 125), "Value")
    page.insert_text((60, 175), "Alice")
    page.insert_text((160, 175), "125")
    table = extract_table_proposals(page)["tables"][0]
    assert table["status"] == "proposed", table["warnings"]
    assert (table["row_count"], table["column_count"]) == (3, 2)
    heading = cell_at(table, 0, 0)
    assert heading["bbox"] == [50, 50, 250, 100]
    assert (heading["row_span"], heading["column_span"]) == (1, 2)
    assert heading["header_candidate"] is True
    assert table["grid"][0] == [heading["id"], heading["id"]]
    assert len(table["cells"]) == 5
    assert [c["raw"] for c in table["cells"]].count("Combined heading") == 1
    assert cell_at(table, 2, 1)["raw"] == "125"


def test_vertical_merge_keeps_amounts_in_their_rows(page):
    page.draw_rect((50, 50, 250, 150))
    page.draw_line((150, 50), (150, 150))
    page.draw_line((150, 100), (250, 100))
    page.insert_text((60, 75), "Alice")
    page.insert_text((160, 75), "100")
    page.insert_text((160, 125), "200")
    table = extract_table_proposals(page)["tables"][0]
    name = cell_at(table, 0, 0)
    assert name["raw"] == "Alice"
    assert (name["row_span"], name["column_span"]) == (2, 1)
    assert name["bbox"] == [50, 50, 150, 150]
    assert table["grid"][0][0] == table["grid"][1][0] == name["id"]
    assert cell_at(table, 0, 1)["raw"] == "100"
    assert cell_at(table, 1, 1)["raw"] == "200"
    assert len(table["cells"]) == 3


def test_multiple_tables_have_unique_stable_table_and_cell_ids(page):
    for top in (50, 300):
        draw_grid(page, ys=(top, top + 50, top + 100))
        page.insert_text((60, top + 25), "A")
        page.insert_text((160, top + 25), "B")
        page.insert_text((60, top + 75), "C")
    tables = extract_table_proposals(page, page_number=7)["tables"]
    assert len(tables) == 2
    ids = [table["id"] for table in tables] + [cell["id"] for table in tables for cell in table["cells"]]
    assert len(ids) == len(set(ids))
    assert all(table["source_page"] == 7 for table in tables)
    assert all(cell["source_page"] == 7 for table in tables for cell in table["cells"])
    assert extract_table_proposals(page, page_number=7)["tables"] == tables
    assert extract_table_proposals(page, page_number=8)["tables"][0]["id"] != tables[0]["id"]


def test_detector_failure_remains_visible_and_does_not_leak_content(page, monkeypatch):
    old_setting = bool(fitz.TOOLS.set_small_glyph_heights())
    def fail(*args, **kwargs):
        fitz.TOOLS.set_small_glyph_heights(not old_setting)
        raise RuntimeError("private document excerpt")
    monkeypatch.setattr(fitz.Page, "find_tables", fail)
    result = extract_table_proposals(page)
    assert result["status"] == "failed" and result["tables"] == []
    assert result["warnings"][0]["code"] == "table_detection_failed"
    assert "RuntimeError" in result["warnings"][0]["message"]
    assert "private document excerpt" not in json.dumps(result)
    assert bool(fitz.TOOLS.set_small_glyph_heights()) == old_setting


def test_unresolved_grid_hole_is_not_relabelled_as_a_blank_cell(page, monkeypatch):
    draw_grid(page)
    page.insert_text((60, 75), "A")
    page.insert_text((160, 75), "B")
    page.insert_text((60, 125), "C")
    finder = page.find_tables(strategy="lines_strict")
    finder.tables[0].cells.remove((150, 100, 250, 150))
    monkeypatch.setattr(fitz.Page, "find_tables", lambda *args, **kwargs: finder)
    table = extract_table_proposals(page)["tables"][0]
    assert table["status"] == "ambiguous"
    assert table["grid"][1][1] is None
    assert len(table["cells"]) == 3
    assert "unresolved_grid_slots" in {w["code"] for w in table["warnings"]}


def test_native_text_disagreement_retains_both_versions(page, monkeypatch):
    draw_grid(page)
    page.insert_text((60, 75), "Original")
    finder = page.find_tables(strategy="lines_strict")
    monkeypatch.setattr(finder.tables[0], "extract", lambda: [["Wrong value", ""], ["", ""]])
    monkeypatch.setattr(fitz.Page, "find_tables", lambda *args, **kwargs: finder)
    table = extract_table_proposals(page)["tables"][0]
    assert table["status"] == "ambiguous"
    assert cell_at(table, 0, 0)["raw"] == "Wrong value"
    assert cell_at(table, 0, 0)["native_text"].strip() == "Original"
    assert "cell_text_disagreement" in {w["code"] for w in table["warnings"]}


def test_cell_extraction_failure_is_not_an_empty_success(page, monkeypatch):
    draw_grid(page)
    page.insert_text((60, 75), "A")
    finder = page.find_tables(strategy="lines_strict")
    def fail():
        raise ValueError("broken extraction")
    monkeypatch.setattr(finder.tables[0], "extract", fail)
    monkeypatch.setattr(fitz.Page, "find_tables", lambda *args, **kwargs: finder)
    result = extract_table_proposals(page)
    assert result["status"] == "failed"
    assert result["warnings"][0]["code"] == "table_structure_failed"


def test_unruled_page_returns_no_proposals_without_claiming_coverage(page):
    page.insert_text((50, 50), "A paragraph does not establish table cells.")
    assert extract_table_proposals(page) == {"status": "none", "tables": [], "warnings": []}


def test_empty_ruled_grid_is_not_certified_as_recognized_content(page):
    draw_grid(page)
    result = extract_table_proposals(page)
    # PyMuPDF 1.25.1 suppresses a fully empty grid from its table collection,
    # while retaining the ruled rectangles in the raw finder cells.
    assert result["status"] == "failed"
    assert result["tables"] == []
    assert "unassigned_ruled_cells" in {w["code"] for w in result["warnings"]}


def test_rotated_page_requires_consistent_source_coordinates(page):
    page.set_rotation(90)
    result = extract_table_proposals(page)
    assert result["status"] == "failed"
    assert result["warnings"][0]["code"] == "table_rotation_required"
    assert page.rotation == 90


def test_overlapping_proposals_cannot_be_adopted_as_independent_tables(page, monkeypatch):
    draw_grid(page)
    page.insert_text((60, 75), "A")
    finder = page.find_tables(strategy="lines_strict")
    finder.tables.append(finder.tables[0])
    monkeypatch.setattr(fitz.Page, "find_tables", lambda *args, **kwargs: finder)
    tables = extract_table_proposals(page)["tables"]
    assert len(tables) == 2
    assert tables[0]["id"] != tables[1]["id"]
    assert all(table["status"] == "ambiguous" for table in tables)
    assert all("overlapping_table_proposals" in {w["code"] for w in table["warnings"]} for table in tables)
