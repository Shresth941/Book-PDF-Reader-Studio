"""Explicit adoption/review of detected native tables without losing source evidence."""
from collections import Counter
import copy
import math
import hashlib
import json

from app.core.review import ReviewError


def validate_table_members(page, table):
    """Check a saved grid before review or rendering; never trust cell metadata alone."""
    def reject():
        raise ReviewError("table_invalid_structure", "This saved table has inconsistent cells, positions or spans. Restore its valid structure before review or export.")
    def integer(value):
        return isinstance(value, int) and not isinstance(value, bool)
    def box(value):
        if (not isinstance(value, (list, tuple)) or len(value) != 4
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value)
                or not 0 <= value[0] < value[2] <= page["width"]
                or not 0 <= value[1] < value[3] <= page["height"]):
            reject()
        return value
    rows, columns = table.get("row_count"), table.get("column_count")
    if not integer(rows) or not integer(columns) or min(rows, columns) < 1 or rows * columns > 10000:
        reject()
    bounds = box(table.get("bbox"))
    cells = table.get("cells")
    if not isinstance(cells, list) or not cells or len(cells) > rows * columns:
        reject()
    grid = [[None] * columns for _ in range(rows)]
    ids, xs, ys = set(), {0: bounds[0], columns: bounds[2]}, {0: bounds[1], rows: bounds[3]}
    for cell in cells:
        if not isinstance(cell, dict) or not isinstance(cell.get("id"), str) or cell["id"] in ids:
            reject()
        ids.add(cell["id"])
        r, c, rs, cs = (cell.get(k) for k in ("row", "column", "row_span", "column_span"))
        if (any(not integer(v) for v in (r, c, rs, cs)) or min(r, c) < 0 or min(rs, cs) < 1
                or r + rs > rows or c + cs > columns):
            reject()
        b = box(cell.get("bbox"))
        for edges, index, value in ((xs, c, b[0]), (xs, c + cs, b[2]), (ys, r, b[1]), (ys, r + rs, b[3])):
            if index in edges and abs(edges[index] - value) > 0.001:
                reject()
            edges[index] = value
        for row in range(r, r + rs):
            for column in range(c, c + cs):
                if grid[row][column] is not None:
                    reject()
                grid[row][column] = cell["id"]
    if any(value is None for row in grid for value in row) or table.get("grid") != grid:
        reject()
    for edges in (xs, ys):
        values = [value for _, value in sorted(edges.items())]
        if any(a >= b for a, b in zip(values, values[1:])):
            reject()
    members = [r for r in page["regions"] if r.get("table_id") == table["id"]]
    if len(members) != len(cells) or {r.get("cell_id") for r in members} != ids:
        reject()
    by_id = {c["id"]: c for c in cells}
    for item in members:
        cell = by_id[item["cell_id"]]
        if item.get("kind") != "table_cell" or item.get("bbox") != cell["bbox"] or any(
                item.get(k) != cell[k] for k in ("row", "column", "row_span", "column_span")):
            reject()


def structure_warning_fingerprint(page):
    proposal = page.get("table_proposals", {})
    warnings = proposal.get("warnings", [])
    if not warnings and proposal.get("status") != "failed":
        return None
    return hashlib.sha256(json.dumps({"status": proposal.get("status"), "warnings": warnings}, sort_keys=True).encode()).hexdigest()


def table_blockers(page):
    issues = []
    fingerprint = structure_warning_fingerprint(page)
    review = page.get("structure_warning_review", {})
    if fingerprint and (review.get("fingerprint") != fingerprint or len(review.get("note", "").strip()) < 10):
        issues.append("Table detection has unresolved warnings. Inspect the whole original and record how every possible table or unassigned grid was accounted for.")
    for index, table in enumerate(page.get("table_proposals", {}).get("tables", [])):
        decision = table.get("decision", {})
        if decision.get("action") == "not_table":
            continue
        if decision.get("action") == "adopt":
            try:
                validate_table_members(page, table)
            except ReviewError as error:
                issues.append(f"Table {index + 1}: {error.message}")
        if decision.get("action") != "adopt" or not table.get("structure_approved"):
            issues.append(f"Table {index + 1}: review cell positions, blank cells, headers and spans, or explain why this is not a table.")
    return issues


def clear_table_approvals(page):
    for table in page.get("table_proposals", {}).get("tables", []):
        table["structure_approved"] = False


def decide_table(page, table_id, action, note, reviewer):
    """Called inside the store's revision-checked transaction; no persistence here."""
    from app.infrastructure.structured_pdf import region
    note = note.strip()
    if len(note) < 10:
        raise ReviewError("table_review_note", "Explain the table boundaries and relationships you checked (at least 10 characters).")
    table = next((t for t in page.get("table_proposals", {}).get("tables", []) if t["id"] == table_id), None)
    if not table:
        raise ReviewError("table_not_found", "Table proposal not found.", 404)
    previous = table.get("decision", {}).get("action")
    if action == "not_table":
        if previous == "adopt":
            raise ReviewError("table_already_adopted", "This table already has reviewable cells. Keep them and correct their text against the original.")
        table["decision"] = {"action": action, "note": note, "reviewer": reviewer}
        return
    if action == "approve_structure":
        if previous != "adopt":
            raise ReviewError("table_not_adopted", "Create reviewable cells before approving their relationships.")
        members = {r.get("cell_id") for r in page["regions"] if r.get("table_id") == table_id}
        if members != {c["id"] for c in table["cells"]}:
            raise ReviewError("table_cells_missing", "Every table cell must be accounted for before structural approval.")
        validate_table_members(page, table)
        ordered = sorted(page["regions"], key=lambda r: r["order"])
        indices = [i for i, r in enumerate(ordered) if r.get("table_id") == table_id]
        expected = [c["id"] for c in sorted(table["cells"], key=lambda c: (c["row"], c["column"]))]
        if (indices != list(range(indices[0], indices[0] + len(indices)))
                or [ordered[i]["cell_id"] for i in indices] != expected):
            raise ReviewError("table_reading_order", "Keep this table's cells together in row and column order before approving its structure.")
        table["structure_approved"] = True
        table["structure_review"] = {"note": note, "reviewer": reviewer}
        return
    if action != "adopt" or previous == "adopt":
        raise ReviewError("table_action", "Choose an unadopted table to create reviewable cells.")
    if table.get("status") == "ambiguous" or not table.get("cells"):
        raise ReviewError("table_ambiguous", "The detector could not establish this table's geometry. Transcribe and review the original regions manually; do not guess cell relationships.")
    box = table["bbox"]
    enclosed, untouched = [], []
    for item in page["regions"]:
        r = item["bbox"]
        overlap = max(0, min(r[2], box[2]) - max(r[0], box[0])) * max(0, min(r[3], box[3]) - max(r[1], box[1]))
        if not overlap:
            untouched.append(item)
            continue
        if not (box[0] - 2 <= r[0] and box[1] - 2 <= r[1] and r[2] <= box[2] + 2 and r[3] <= box[3] + 2):
            raise ReviewError("table_crossing_region", "A source paragraph crosses the table boundary. Automatic replacement is blocked to preserve it.")
        # The native extractor creates paragraph regions with uncertain language
        # and the default text disposition. A saved review choice is work even
        # when its text is unchanged and it has not yet been approved. Preserve
        # it in the active page instead of resetting it in replacement cells.
        if (item.get("method") != "native" or item.get("table_id") or item.get("cell_id")
                or item.get("kind", "paragraph") != "paragraph" or item.get("source") != item.get("raw")
                or item.get("translation", "").strip() or item.get("source_approved") or item.get("translation_approved")
                or item.get("source_language", "uncertain") != "uncertain" or item.get("contains_text") is not True
                or item.get("disposition") != ("translate" if item.get("raw", "").strip() else "unreadable")
                or item.get("reason", "").strip() or item.get("acknowledged")
                or item.get("form_evidence") is not None or item.get("form_evidence_changed")
                or item.get("form_review_note", "").strip()):
            raise ReviewError("table_existing_review", "This table overlaps reviewed, edited or non-native content. Keep that work; automatic cell replacement is blocked.")
        enclosed.append(item)
    native_tokens = Counter(" ".join(r["raw"] for r in enclosed).split())
    cell_tokens = Counter(" ".join(c["raw"] for c in table["cells"]).split())
    if native_tokens != cell_tokens:
        raise ReviewError("table_content_mismatch", "The cell proposal does not account for every native word exactly once. No source region was replaced.")
    cells = []
    for cell in table["cells"]:
        coords = cell["bbox"]
        if (len(coords) != 4 or not all(math.isfinite(x) for x in coords)
                or not 0 <= coords[0] < coords[2] <= page["width"]
                or not 0 <= coords[1] < coords[3] <= page["height"]):
            raise ReviewError("table_cell_bounds", "A cell extends beyond the page. Correct the structure before adoption.")
        empty = not cell["raw"].strip()
        cells.append(region("table_cell", coords, cell["raw"], table_id=table_id, cell_id=cell["id"],
            row=cell["row"], column=cell["column"], row_span=cell["row_span"], column_span=cell["column_span"],
            disposition="decorative" if empty else "translate", contains_text=not empty,
            reason="Detected empty table cell; check the original." if empty else "",
            evidence=["Review this cell with its row and column headers, including empty or merged cells."]))
    cells.sort(key=lambda r: (r["row"], r["column"]))
    insertion = min((r["order"] for r in enclosed), default=len(page["regions"]))
    ordered = sorted(untouched, key=lambda r: r["order"])
    index = next((i for i, r in enumerate(ordered) if r["order"] >= insertion), len(ordered))
    page["regions"] = ordered[:index] + cells + ordered[index:]
    for order, item in enumerate(page["regions"]):
        item["order"] = order
    table["original_regions"] = copy.deepcopy(enclosed)
    table["decision"] = {"action": "adopt", "note": note, "reviewer": reviewer}
    table["structure_approved"] = False
