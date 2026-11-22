"""Explicit, bounded manual grids; source evidence is archived, never inferred."""
from copy import deepcopy
import math
from uuid import uuid4

from app.core.review import ReviewError


def _fail(code, message):
    raise ReviewError(code, message)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _edges(values, limit, maximum):
    if (not isinstance(values, list) or not 2 <= len(values) <= maximum + 1
            or not all(_number(value) for value in values)
            or values[0] < 0 or values[-1] > limit
            or any(right - left < 1 for left, right in zip(values, values[1:]))):
        _fail("manual_table_edges", "Use ordered boundaries inside the page, at least one point apart, within the row and column limits.")
    return list(values)


def _box(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or not all(_number(v) for v in value)
            or value[0] >= value[2] or value[1] >= value[3]):
        _fail("manual_table_geometry", "Existing source geometry is invalid. No table content was replaced.")
    return value


def _overlaps(left, right):
    return min(left[2], right[2]) > max(left[0], right[0]) and min(left[3], right[3]) > max(left[1], right[1])


def create_manual_table(page, body, reviewer):
    """Apply only after validating the complete request; caller owns revisions/commit.

    Coordinates are absolute, unrotated PDF points. Every intersecting source
    region must be explicitly selected and wholly contained. Existing edits,
    forms and adopted cells cannot be replaced by this intentionally narrow path.
    """
    from app.infrastructure.structured_pdf import region

    if page.get("status") != "extracted":
        _fail("not_extracted", "Extract or record manual transcription for this page before creating a table.")
    if not all(_number(page.get(key)) and page[key] > 0 for key in ("width", "height")):
        _fail("manual_table_geometry", "Page dimensions are invalid.")
    note = body.get("note", "")
    if not isinstance(note, str) or not 10 <= len(note.strip()) <= 4000:
        _fail("manual_table_note", "Record how the grid and every replaced source region were checked (10 to 4,000 characters).")
    xs = _edges(body.get("x_edges"), page["width"], 20)
    ys = _edges(body.get("y_edges"), page["height"], 40)
    rows, columns = len(ys) - 1, len(xs) - 1
    if rows * columns > 200:
        _fail("manual_table_limit", "A manual grid is limited to 200 row and column positions.")
    box = [xs[0], ys[0], xs[-1], ys[-1]]

    merges = body.get("merges", [])
    if not isinstance(merges, list) or len(merges) > 200:
        _fail("manual_table_merge", "Use at most 200 explicit merged-cell ranges.")
    slots = [[None for _ in range(columns)] for _ in range(rows)]
    spans = []
    for merge in merges:
        keys = ("row", "column", "row_span", "column_span")
        if (not isinstance(merge, dict) or set(merge) != set(keys)
                or any(not isinstance(merge[key], int) or isinstance(merge[key], bool) for key in keys)):
            _fail("manual_table_merge", "Merged cells need integer row, column and span values.")
        row, column, row_span, column_span = [merge[key] for key in keys]
        if (min(row, column) < 0 or min(row_span, column_span) < 1
                or row + row_span > rows or column + column_span > columns):
            _fail("manual_table_merge", "A merged cell extends beyond this grid.")
        for r in range(row, row + row_span):
            for c in range(column, column + column_span):
                if slots[r][c] is not None:
                    _fail("manual_table_merge", "Merged-cell ranges overlap. No grid was created.")
                slots[r][c] = len(spans)
        spans.append((row, column, row_span, column_span))
    for row in range(rows):
        for column in range(columns):
            if slots[row][column] is None:
                slots[row][column] = len(spans)
                spans.append((row, column, 1, 1))

    proposal_root = page.get("table_proposals", {"status": "none", "tables": [], "warnings": []})
    proposals = proposal_root.get("tables", [])
    replace_table_id = body.get("replace_table_id")
    replacement = next((table for table in proposals if table.get("id") == replace_table_id), None) if replace_table_id else None
    if replace_table_id and (replacement is None or replacement.get("method") in ("manual_grid", "manual-table")):
        _fail("manual_table_proposal", "Choose an existing unadopted detector proposal to replace.")
    if len(proposals) + (0 if replacement else 1) > 64:
        _fail("manual_table_limit", "A page is limited to 64 table structures.")
    for table in proposals:
        overlaps = _overlaps(box, _box(table["bbox"]))
        adopted = table.get("decision", {}).get("action") == "adopt"
        if table is replacement:
            if adopted or not overlaps:
                _fail("manual_table_proposal", "Only an overlapping, unadopted detector proposal can be replaced.")
        elif overlaps and (adopted or table.get("decision", {}).get("action") != "not_table"):
            _fail("manual_table_proposal_overlap", "This grid overlaps another table proposal. Resolve its boundaries before creating this grid.")

    selected = body.get("replace_region_ids", [])
    if (not isinstance(selected, list) or any(not isinstance(value, str) for value in selected)
            or len(selected) != len(set(selected))):
        _fail("manual_table_mapping", "Select every intersecting source region exactly once.")
    originals, untouched = [], []
    for item in page["regions"]:
        source_box = _box(item["bbox"])
        if not _overlaps(box, source_box):
            untouched.append(item)
            continue
        if not (box[0] <= source_box[0] and box[1] <= source_box[1] and source_box[2] <= box[2] and source_box[3] <= box[3]):
            _fail("manual_table_crossing_region", "A source region crosses this table boundary. Keep it intact; a smaller grid cannot replace content outside its bounds.")
        if (item.get("table_id") or item.get("cell_id") or item.get("method") not in ("native", "image-ocr")
                or item.get("kind") != ("paragraph" if item.get("method") == "native" else "image")
                or item.get("form_evidence") is not None or item.get("form_evidence_changed") or item.get("acknowledged")
                or item.get("source") != item.get("raw")
                or item.get("translation", "").strip() or item.get("source_approved") or item.get("translation_approved")
                or item.get("reason", "").strip() or item.get("form_review_note", "").strip()
                or item.get("source_language", "uncertain") != "uncertain"
                or item.get("contains_text") is not True
                or item.get("disposition") != ("translate" if item.get("raw", "").strip() else "unreadable")):
            _fail("manual_table_existing_review", "The grid overlaps existing edits, approvals, a form or table cell. That work must remain intact.")
        originals.append(item)
    if set(selected) != {item["id"] for item in originals} or len(selected) != len(originals):
        _fail("manual_table_mapping", "Explicitly select exactly every source region replaced by this grid, including empty recognition regions.")
    orders = [item.get("order") for item in page["regions"]]
    if any(not isinstance(order, int) or isinstance(order, bool) for order in orders) or len(set(orders)) != len(orders):
        _fail("manual_table_order", "Existing reading order is invalid. No grid was created.")

    table_id = str(uuid4())
    cells, regions = [], []
    grid = [[None for _ in range(columns)] for _ in range(rows)]
    for row, column, row_span, column_span in sorted(spans):
        cell_id = str(uuid4())
        cell_box = [xs[column], ys[row], xs[column + column_span], ys[row + row_span]]
        cell = {"id": cell_id, "source_page": page["number"], "row": row, "column": column,
                "row_span": row_span, "column_span": column_span, "bbox": cell_box,
                "raw": "", "native_text": "", "is_blank": None, "header_candidate": False}
        cells.append(cell)
        regions.append(region("table_cell", cell_box, table_id=table_id, cell_id=cell_id,
                              row=row, column=column, row_span=row_span, column_span=column_span,
                              method="manual-table", disposition="unreadable", contains_text=True,
                              evidence=["Manually drawn cell. Transcribe visible content or explicitly confirm it is blank against the original; no text was inferred."]))
        for r in range(row, row + row_span):
            for c in range(column, column + column_span):
                grid[r][c] = cell_id
    table = {"id": table_id, "source_page": page["number"], "method": "manual_grid", "bbox": box,
             "status": "manual", "review_required": True, "confidence": None,
             "confidence_reason": "Manual geometry is unapproved; every cell and relationship requires review.",
             "warnings": [], "row_count": rows, "column_count": columns, "cells": cells, "grid": grid,
             "header": None, "x_edges": xs, "y_edges": ys, "original_regions": deepcopy(originals),
             "decision": {"action": "adopt", "note": note.strip(), "reviewer": reviewer, "method": "manual_grid"},
             "structure_approved": False}
    if replacement:
        table["original_proposal"] = deepcopy(replacement)
    insertion = min((item["order"] for item in originals), default=len(page["regions"]))
    ordered = deepcopy(sorted(untouched, key=lambda item: item["order"]))
    index = next((i for i, item in enumerate(ordered) if item["order"] >= insertion), len(ordered))
    result_regions = ordered[:index] + regions + ordered[index:]
    for order, item in enumerate(result_regions):
        item["order"] = order
    result_proposals = deepcopy(proposal_root)
    result_proposals["tables"] = [table if item is replacement else deepcopy(item) for item in proposals]
    if replacement is None:
        result_proposals["tables"].append(table)
    if result_proposals.get("status") == "none":
        result_proposals["status"] = "proposed"
    # No operation above mutates the supplied page. Publish the complete result.
    page.update(regions=result_regions, table_proposals=result_proposals, coverage_approved=False)
    return table
