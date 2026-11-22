"""Manual table geometry must never silently consume other content or reviews."""
from copy import deepcopy
from uuid import uuid4

import pytest

from app.core.manual_table import create_manual_table
from app.core.review import ReviewError, page_blockers
from app.core.structure_review import decide_table, table_blockers, validate_table_members
from app.infrastructure.structured_pdf import region
from app.services.table_export import content_units


NOTE = "Compared the original table rectangle, cell boundaries and every replaced source region."


@pytest.fixture
def manual_page():
    items = [region("paragraph", [50, 30, 200, 45], "Before"),
             region("paragraph", [60, 90, 90, 110], "Name"),
             region("paragraph", [160, 90, 200, 110], "Amount"),
             region("paragraph", [60, 140, 95, 160], "Alice"),
             region("paragraph", [50, 250, 200, 265], "After")]
    for order, item in enumerate(items):
        item.update(order=order, evidence=["Original detector evidence"], alternatives=[{"method": "test", "text": item["raw"]}])
    return {"number": 1, "revision": 3, "status": "extracted", "width": 600, "height": 800,
            "regions": items, "coverage_approved": True,
            "table_proposals": {"status": "none", "tables": [], "warnings": []}}


def definition(page):
    return {"x_edges": [50, 150, 250], "y_edges": [80, 130, 180], "merges": [],
            "replace_region_ids": [r["id"] for r in page["regions"][1:4]], "replace_table_id": None, "note": NOTE}


def test_manual_creation_archives_exact_source_and_leaves_even_blank_cells_unresolved(manual_page):
    before = deepcopy(manual_page)
    table = create_manual_table(manual_page, definition(manual_page), "Reviewer")
    assert table["original_regions"] == before["regions"][1:4]
    assert table["decision"]["action"] == "adopt" and not table["structure_approved"]
    assert table["method"] == "manual_grid" and not manual_page["coverage_approved"]
    assert manual_page["regions"][0] == before["regions"][0]
    assert manual_page["regions"][-1]["source"] == "After"
    cells = [r for r in manual_page["regions"] if r.get("table_id")]
    assert len(cells) == 4 and [r["order"] for r in manual_page["regions"]] == list(range(6))
    for item in cells:
        assert item["raw"] == item["source"] == item["translation"] == ""
        assert item["disposition"] == "unreadable" and item["contains_text"]
        assert not item["source_approved"] and not item["translation_approved"]
    assert all(c["is_blank"] is None for c in table["cells"])
    validate_table_members(manual_page, table)
    assert page_blockers(manual_page, [], "en")
    decide_table(manual_page, table["id"], "approve_structure", NOTE, "Reviewer")
    assert not table_blockers(manual_page)  # Content review remains separately blocked.
    assert page_blockers(manual_page, [], "en")


def test_unequal_boundaries_and_merged_cells_cover_every_slot_once(manual_page):
    body = definition(manual_page)
    body.update(x_edges=[50, 115, 250], y_edges=[80, 120, 180], merges=[{"row": 0, "column": 0, "row_span": 1, "column_span": 2}])
    table = create_manual_table(manual_page, body, "Reviewer")
    assert len(table["cells"]) == 3
    assert table["grid"][0][0] == table["grid"][0][1]
    assert table["cells"][0]["bbox"] == [50, 80, 250, 120]
    validate_table_members(manual_page, table)
    assert [u["type"] for u in content_units(manual_page)] == ["region", "table", "region"]


@pytest.mark.parametrize("changes", [
    {"x_edges": [50, 50, 250]}, {"x_edges": [50, 49, 250]}, {"x_edges": [50, 50.5, 250]},
    {"x_edges": [False, 150, 250]}, {"x_edges": [50, float("nan"), 250]},
    {"x_edges": [50, float("inf")]}, {"x_edges": [-1, 250]}, {"x_edges": [50, 601]},
    {"x_edges": [50]}, {"x_edges": list(range(22))}, {"y_edges": list(range(42))},
    {"x_edges": list(range(21)), "y_edges": list(range(12))},
    {"replace_region_ids": []}, {"replace_region_ids": ["missing"]},
    {"note": "short"}, {"note": " " * 20}, {"replace_table_id": str(uuid4())},
    {"merges": [{"row": 0, "column": 0, "row_span": 3, "column_span": 1}]},
    {"merges": [{"row": -1, "column": 0, "row_span": 1, "column_span": 1}]},
    {"merges": [{"row": 0, "column": 0, "row_span": 0, "column_span": 1}]},
    {"merges": [{"row": True, "column": 0, "row_span": 1, "column_span": 1}]},
    {"merges": [{"row": 0, "column": 0, "row_span": 1, "column_span": 2}, {"row": 0, "column": 1, "row_span": 1, "column_span": 1}]},
])
def test_invalid_definition_is_atomic(manual_page, changes):
    body = {**definition(manual_page), **changes}
    before = deepcopy(manual_page)
    with pytest.raises(ReviewError):
        create_manual_table(manual_page, body, "Reviewer")
    assert manual_page == before


@pytest.mark.parametrize("change", [
    {"bbox": [20, 90, 90, 110]}, {"source": "User corrected name"}, {"translation": "Saved translation"},
    {"source_approved": True}, {"translation_approved": True}, {"method": "form-field"},
    {"method": "manual"}, {"kind": "table_cell"}, {"table_id": "existing"},
    {"reason": "Saved reason"}, {"source_language": "en"}, {"disposition": "decorative"},
    {"kind": "heading"}, {"kind": "footnote"}, {"cell_id": "saved-cell"},
    {"form_evidence": {}}, {"form_evidence_changed": True}, {"acknowledged": {"review": "Saved decision"}},
    {"contains_text": False},
])
def test_crossing_source_and_prior_work_are_not_replaced(manual_page, change):
    manual_page["regions"][1].update(change)
    before = deepcopy(manual_page)
    with pytest.raises(ReviewError):
        create_manual_table(manual_page, definition(manual_page), "Reviewer")
    assert manual_page == before


def test_isolated_image_table_keeps_ocr_alternatives_without_inventing_cells(manual_page):
    scan = region("image", [50, 80, 250, 180], method="image-ocr", alternatives=[{"method": "tesseract", "text": "uncertain original"}])
    scan["order"] = 1
    manual_page["regions"] = [manual_page["regions"][0], scan]
    body = {**definition(manual_page), "replace_region_ids": [scan["id"]]}
    table = create_manual_table(manual_page, body, "Reviewer")
    assert table["original_regions"] == [scan]
    assert all(r["source"] == "" for r in manual_page["regions"] if r.get("table_id"))


def test_replacing_ambiguous_proposal_preserves_detector_evidence(manual_page):
    old = {"id": str(uuid4()), "bbox": [50, 80, 250, 180], "method": "pymupdf_native_lines", "status": "ambiguous", "cells": [], "warnings": [{"code": "test", "message": "Ambiguous geometry"}]}
    manual_page["table_proposals"] = {"status": "failed", "tables": [old], "warnings": [{"code": "page_warning", "message": "Check all possible grids"}]}
    body = definition(manual_page)
    before = deepcopy(manual_page)
    with pytest.raises(ReviewError, match="another table"):
        create_manual_table(manual_page, body, "Reviewer")
    assert manual_page == before
    table = create_manual_table(manual_page, {**body, "replace_table_id": old["id"]}, "Reviewer")
    assert table["original_proposal"] == old
    assert manual_page["table_proposals"]["warnings"] == before["table_proposals"]["warnings"]
    assert len(manual_page["table_proposals"]["tables"]) == 1


@pytest.mark.parametrize("fault", ["duplicate_region", "wrong_bbox", "wrong_span", "duplicate_cell", "missing_slot", "overlap_geometry"])
def test_inconsistent_saved_table_blocks_structure_and_exports(manual_page, fault):
    table = create_manual_table(manual_page, definition(manual_page), "Reviewer")
    cells = [r for r in manual_page["regions"] if r.get("table_id")]
    if fault == "duplicate_region": manual_page["regions"].append(deepcopy(cells[0]))
    elif fault == "wrong_bbox": cells[0]["bbox"] = [55, 80, 150, 130]
    elif fault == "wrong_span": cells[0]["row_span"] = 2
    elif fault == "duplicate_cell": table["cells"][1]["id"] = table["cells"][0]["id"]
    elif fault == "missing_slot": table["grid"][1][1] = None
    else: table["cells"][1]["bbox"][0] = 100
    before = deepcopy(manual_page)
    assert table_blockers(manual_page)
    with pytest.raises(ReviewError): decide_table(manual_page, table["id"], "approve_structure", NOTE, "Reviewer")
    with pytest.raises(ReviewError): list(content_units(manual_page))
    assert manual_page == before
