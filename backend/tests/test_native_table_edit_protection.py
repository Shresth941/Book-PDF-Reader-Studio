"""Adoption must preserve saved review choices even before content approval."""
from copy import deepcopy

import pytest

from app.core.review import ReviewError
from app.core.structure_review import decide_table, validate_table_members
from app.infrastructure.structured_pdf import region


NOTE = "Compared the native table, all cell boundaries and source regions."


@pytest.fixture
def native_page():
    before = region("paragraph", [10, 10, 150, 25], "Before the table")
    first = region("paragraph", [25, 60, 70, 75], "Name")
    second = region("paragraph", [125, 60, 175, 75], "Amount")
    after = region("paragraph", [10, 180, 150, 195], "After the table")
    regions = [before, first, second, after]
    for order, item in enumerate(regions):
        item["order"] = order
        item["evidence"] = ["Original native evidence remains available."]
    cells = [
        {"id": "left", "bbox": [20, 50, 110, 100], "row": 0, "column": 0,
         "row_span": 1, "column_span": 1, "raw": "Name"},
        {"id": "right", "bbox": [110, 50, 200, 100], "row": 0, "column": 1,
         "row_span": 1, "column_span": 1, "raw": "Amount"},
    ]
    table = {"id": "native-table", "bbox": [20, 50, 200, 100], "status": "proposed",
             "cells": cells, "grid": [["left", "right"]], "row_count": 1,
             "column_count": 2, "structure_approved": False}
    return {"number": 1, "width": 300, "height": 400, "regions": regions,
            "table_proposals": {"status": "proposed", "tables": [table], "warnings": []}}


@pytest.mark.parametrize("changes", [
    pytest.param({"source_language": "en"}, id="confirmed-english"),
    pytest.param({"source_language": "hi"}, id="confirmed-hindi"),
    pytest.param({"source_language": "mixed"}, id="mixed-language"),
    pytest.param({"disposition": "preserve"}, id="preserved-visually"),
    pytest.param({"disposition": "already_target"}, id="already-target"),
    pytest.param({"disposition": "decorative"}, id="decorative"),
    pytest.param({"disposition": "excluded"}, id="explicitly-excluded"),
    pytest.param({"disposition": "unreadable"}, id="marked-unreadable"),
    pytest.param({"contains_text": False}, id="nontext-choice"),
    pytest.param({"reason": "Reviewer recorded why this source must be preserved."}, id="saved-reason"),
    pytest.param({"kind": "heading"}, id="heading-choice"),
    pytest.param({"kind": "footnote"}, id="footnote-choice"),
    pytest.param({"kind": "table_cell"}, id="orphan-table-cell"),
    pytest.param({"table_id": "prior-table"}, id="existing-table"),
    pytest.param({"cell_id": "prior-cell"}, id="orphan-cell-marker"),
    pytest.param({"form_evidence": {}}, id="incomplete-form-evidence"),
    pytest.param({"form_evidence": {"xref": 7}}, id="retained-form-evidence"),
    pytest.param({"form_evidence_changed": True}, id="form-change-review"),
    pytest.param({"form_review_note": "Compared stored and visible field values."}, id="form-review-note"),
    pytest.param({"acknowledged": {"numbers_changed": "Checked against the source."}}, id="saved-check-review"),
])
def test_native_adoption_refuses_saved_choices_atomically(native_page, changes):
    # Edit the second enclosed region: encountering the first untouched region
    # must not allow even a partial replacement before the later edit is found.
    native_page["regions"][2].update(changes)
    original = deepcopy(native_page)
    with pytest.raises(ReviewError) as error:
        decide_table(native_page, "native-table", "adopt", NOTE, "Reviewer")
    assert error.value.code == "table_existing_review"
    assert native_page == original


def test_native_untouched_content_still_adopts_and_archives_source(native_page):
    original = deepcopy(native_page)
    decide_table(native_page, "native-table", "adopt", NOTE, "Reviewer")
    table = native_page["table_proposals"]["tables"][0]
    assert table["decision"]["action"] == "adopt"
    assert not table["structure_approved"]
    assert table["original_regions"] == original["regions"][1:3]
    assert [r["source"] for r in native_page["regions"]] == [r["source"] for r in original["regions"]]
    assert [r["order"] for r in native_page["regions"]] == [0, 1, 2, 3]
    assert all(not r["source_approved"] and not r["translation_approved"] for r in native_page["regions"])
    validate_table_members(native_page, table)


def test_review_choices_outside_table_remain_unchanged(native_page):
    native_page["regions"][0].update(source_language="en", disposition="already_target",
                                     reason="Confirmed introduction language.", source_approved=True)
    native_page["regions"][-1].update(contains_text=False, disposition="decorative",
                                      reason="Reviewed the closing ornament.")
    before, after = deepcopy(native_page["regions"][0]), deepcopy(native_page["regions"][-1])
    decide_table(native_page, "native-table", "adopt", NOTE, "Reviewer")
    assert native_page["regions"][0] == before
    assert native_page["regions"][-1] == after
