"""Unreviewed native ruled-table proposals; never removes source text regions.

Coordinates are the unrotated page coordinates used by structured_pdf. All row
and column indices are zero based. A merged cell appears once in ``cells``;
every slot it covers in ``grid`` references that same cell id. A null grid slot
is unresolved geometry, never a fabricated blank cell. ``is_blank`` means both
native text signals are empty; it does not certify the absence of image content.
"""
from __future__ import annotations

import math
from uuid import NAMESPACE_URL, uuid5

import fitz


METHOD = "pymupdf_native_lines"
MAX_TABLES = 64
MAX_GRID_SLOTS = 10000


def _warning(code, message):
    return {"code": code, "message": message}


def _box(value):
    values = list(value)
    if len(values) != 4 or not all(math.isfinite(v) for v in values):
        raise ValueError("Invalid table coordinates")
    if not (values[0] < values[2] and values[1] < values[3]):
        raise ValueError("Empty or inverted table coordinates")
    return values


def _stable_id(source_page, table_index, bbox, suffix=""):
    geometry = ",".join(float(v).hex() for v in bbox)
    return str(uuid5(NAMESPACE_URL, f"pdf-reader-table:{source_page}:{table_index}:{geometry}:{suffix}"))


def _header_proposal(table, warnings):
    header = getattr(table, "header", None)
    if header is None:
        warnings.append(_warning("header_unavailable", "The table finder did not provide a header proposal. Identify headers during review."))
        return None
    try:
        return {"bbox": _box(header.bbox),
                "cells": [None if cell is None else _box(cell) for cell in header.cells],
                "names": list(header.names), "external": bool(header.external),
                "review_required": True}
    except (AttributeError, TypeError, ValueError):
        warnings.append(_warning("header_ambiguous", "The header proposal has inconsistent geometry. Identify headers manually."))
        return None


def _table_proposal(page, source_page, table_index, table):
    bbox = _box(table.bbox)
    warnings = []
    proposal = {"id": _stable_id(source_page, table_index, bbox), "source_page": source_page,
                "method": METHOD, "bbox": bbox, "status": "proposed", "review_required": True,
                "confidence": None, "confidence_reason": "No calibrated table confidence is available. Geometry and meaning require review.",
                "warnings": warnings}
    raw_boxes = [_box(cell) for cell in table.cells if cell is not None]
    unique_boxes = sorted({tuple(cell) for cell in raw_boxes}, key=lambda cell: (cell[1], cell[0], cell[3], cell[2]))
    if not unique_boxes:
        raise ValueError("No table cells")
    if len(unique_boxes) != len(raw_boxes):
        warnings.append(_warning("duplicate_cell_geometry", "The finder returned duplicate cell boxes. Review the source before adopting this table."))
    xs = sorted({v for cell in unique_boxes for v in (cell[0], cell[2])})
    ys = sorted({v for cell in unique_boxes for v in (cell[1], cell[3])})
    row_count, column_count = len(ys) - 1, len(xs) - 1
    if row_count * column_count > MAX_GRID_SLOTS:
        proposal.update(status="ambiguous", row_count=row_count, column_count=column_count,
                        cells=[], grid=[], header=None)
        warnings.append(_warning("table_grid_limit", "This proposed table exceeds the bounded grid limit. Review its native regions manually."))
        return proposal
    if any(b - a < 0.1 for edges in (xs, ys) for a, b in zip(edges, edges[1:])):
        warnings.append(_warning("nearly_coincident_edges", "Some grid boundaries are nearly coincident. They were retained exactly instead of guessed or merged."))
    if row_count != table.row_count or column_count != table.col_count:
        warnings.append(_warning("grid_dimensions_disagree", "Cell boundaries and the finder's row or column counts disagree. Review all merged cells."))
    if (xs[0], ys[0], xs[-1], ys[-1]) != tuple(bbox):
        warnings.append(_warning("table_bounds_disagree", "Cell boundaries do not cover the declared table rectangle."))

    # Read both signals immediately: PyMuPDF 1.25.1 table.extract uses module
    # globals replaced by the next find_tables call. No Table object escapes.
    extracted = table.extract()
    text_by_box = {}
    rows = table.rows
    if len(extracted) != len(rows):
        warnings.append(_warning("cell_text_alignment", "Extracted text does not match the detected table rows."))
    for row_index, row in enumerate(rows):
        row_text = extracted[row_index] if row_index < len(extracted) else []
        if len(row_text) != len(row.cells):
            warnings.append(_warning("cell_text_alignment", "Extracted text does not match the detected row cells."))
        for column_index, cell in enumerate(row.cells):
            value = row_text[column_index] if column_index < len(row_text) else None
            if cell is None:
                if value not in (None, ""):
                    warnings.append(_warning("unlocated_cell_text", "The finder returned text without a corresponding cell rectangle."))
                continue
            key = tuple(_box(cell))
            if key in text_by_box and text_by_box[key] != value:
                warnings.append(_warning("duplicate_cell_text", "One cell rectangle has conflicting extracted text."))
            text_by_box[key] = value

    header = _header_proposal(table, warnings)
    if header and len(header["names"]) != column_count:
        warnings.append(_warning("header_column_mismatch", "Header labels do not align with the proposed column count."))
    grid = [[None for _ in range(column_count)] for _ in range(row_count)]
    cells = []
    any_text = False
    for cell_box in unique_boxes:
        x0, y0, x1, y1 = cell_box
        row, column = ys.index(y0), xs.index(x0)
        row_span, column_span = ys.index(y1) - row, xs.index(x1) - column
        cell_id = _stable_id(source_page, table_index, cell_box, f"table:{proposal['id']}:cell:{row}:{column}")
        raw = text_by_box.get(cell_box)
        native = page.get_text("text", clip=fitz.Rect(cell_box), sort=False)
        if not isinstance(raw, str):
            warnings.append(_warning("cell_text_missing", f"Cell at row {row + 1}, column {column + 1} has no aligned native extraction."))
            raw = None
        elif " ".join(raw.split()) != " ".join(native.split()):
            warnings.append(_warning("cell_text_disagreement", f"Two native extraction methods disagree at row {row + 1}, column {column + 1}. Both versions were preserved."))
        any_text = any_text or bool((raw or "").strip()) or bool(native.strip())
        for r in range(row, row + row_span):
            for c in range(column, column + column_span):
                if grid[r][c] is not None:
                    warnings.append(_warning("overlapping_cells", "Detected cell rectangles overlap. The proposed grid cannot be adopted automatically."))
                    # Keep the first assignment; every competing cell and its
                    # exact rectangle remain in cells for manual inspection.
                else:
                    grid[r][c] = cell_id
        cells.append({"id": cell_id, "source_page": source_page, "row": row, "column": column,
                      "row_span": row_span, "column_span": column_span, "bbox": list(cell_box),
                      "raw": raw, "native_text": native, "is_blank": raw == "" and not native.strip() if raw is not None else None,
                      "header_candidate": bool(header and not header["external"] and row == 0)})
    if any(cell is None for row in grid for cell in row):
        warnings.append(_warning("unresolved_grid_slots", "Some grid positions have no detected cell. They are unresolved, not confirmed blank cells."))
    if not any_text:
        warnings.append(_warning("no_native_table_text", "No native text was found in this grid. It may be an empty form or contain image-based text requiring separate review."))
    proposal.update(row_count=row_count, column_count=column_count, cells=cells, grid=grid, header=header,
                    status="ambiguous" if warnings else "proposed")
    return proposal


def extract_table_proposals(page, page_number=None):
    """Return plain JSON-compatible proposals without changing source regions.

    Call inside the bounded PDF worker on an unrotated page. Empty detection is
    not proof that the page has no table: borderless and image-based tables are
    outside this helper's scope. Structural or finder failures remain visible.
    """
    source_page = page.number + 1 if page_number is None else page_number
    result = {"status": "none", "tables": [], "warnings": []}
    if page.rotation:
        result["status"] = "failed"
        result["warnings"].append(_warning("table_rotation_required", "Use the unrotated source-page coordinate space before proposing tables."))
        return result
    previous_small_glyphs = bool(fitz.TOOLS.set_small_glyph_heights())
    try:
        finder = page.find_tables(vertical_strategy="lines_strict", horizontal_strategy="lines_strict")
        tables = sorted(finder.tables, key=lambda table: (table.bbox[1], table.bbox[0], table.bbox[3], table.bbox[2]))
        assigned = {tuple(cell) for table in tables for cell in table.cells if cell is not None}
        unassigned = [cell for cell in getattr(finder, "cells", []) if cell is not None and tuple(cell) not in assigned]
        if unassigned:
            # 1.25.1 drops all-textless grids from finder.tables even though it
            # has located their ruled cells. Do not report a clean no-table
            # result for an empty form or an image-based table with vector rules.
            result["warnings"].append(_warning("unassigned_ruled_cells", f"{len(unassigned)} ruled cell rectangles could not be assigned to a native-text table. Review possible empty forms or image-based content on the source page."))
        if len(tables) > MAX_TABLES:
            result["warnings"].append(_warning("table_count_limit", "The page exceeds the table-proposal limit. Additional source regions still require manual review."))
        for index, table in enumerate(tables[:MAX_TABLES]):
            try:
                result["tables"].append(_table_proposal(page, source_page, index, table))
            except Exception as exc:
                result["warnings"].append(_warning("table_structure_failed", f"Table {index + 1} could not be represented safely ({type(exc).__name__}). Keep and review its source regions."))
        for index, table in enumerate(result["tables"]):
            for other in result["tables"][index + 1:]:
                if (fitz.Rect(table["bbox"]) & fitz.Rect(other["bbox"])).get_area() > 0:
                    for affected in (table, other):
                        affected["status"] = "ambiguous"
                        affected["warnings"].append(_warning("overlapping_table_proposals", "Table proposals overlap. Review their separate boundaries before adopting either structure."))
        result["status"] = "proposed" if result["tables"] else "failed" if result["warnings"] else "none"
    except Exception as exc:
        result["status"] = "failed"
        result["warnings"].append(_warning("table_detection_failed", f"Native table detection failed ({type(exc).__name__}). Review the source page manually."))
    finally:
        fitz.TOOLS.set_small_glyph_heights(previous_small_glyphs)
    return result
