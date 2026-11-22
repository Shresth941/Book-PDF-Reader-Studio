"""Render reviewed native table relationships without flattening values into prose."""
from app.core.review import ReviewError
import io


def visual_cell(item, source, source_page):
    import fitz
    page = source[source_page - 1]
    page.set_rotation(0)
    box = fitz.Rect(item["bbox"]) & page.rect
    if box.is_empty:
        raise ReviewError("table_visual_bounds", "A preserved table cell has no visible source rectangle.")
    scale = min(2, 1200 / max(box.width, box.height))
    return io.BytesIO(page.get_pixmap(clip=box, matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png"))


def content_units(page):
    ordered = sorted(page["regions"], key=lambda r: r["order"])
    tables = {t["id"]: t for t in page.get("table_proposals", {}).get("tables", [])
              if t.get("decision", {}).get("action") == "adopt"}
    seen = set()
    for index, region in enumerate(ordered):
        table_id = region.get("table_id")
        if not table_id:
            yield {"type": "region", "region": region}
            continue
        if table_id in seen:
            continue
        table = tables.get(table_id)
        if not table:
            raise ReviewError("table_export_structure", "A table cell has no adopted structure. Review the table before export.")
        cells = sorted(table["cells"], key=lambda c: (c["row"], c["column"]))
        members = [r for r in ordered if r.get("table_id") == table_id]
        if ([r.get("cell_id") for r in members] != [c["id"] for c in cells]
                or ordered[index:index + len(members)] != members):
            raise ReviewError("table_export_order", "Keep all cells of a table together in their approved row and column order before export.")
        from app.core.structure_review import validate_table_members
        validate_table_members(page, table)
        seen.add(table_id)
        yield {"type": "table", "table": table, "regions": members}


def render_pdf_table(pdf, unit, source_page, text_for, source):
    from fpdf.enums import MethodReturnValue
    table, regions = unit["table"], unit["regions"]
    cells = sorted(table["cells"], key=lambda c: (c["row"], c["column"]))
    by_cell = {r["cell_id"]: r for r in regions}
    row_count = table["row_count"]
    original_width = table["bbox"][2] - table["bbox"][0]
    scale = pdf.epw / original_width
    pdf.set_font("Review", size=11)
    heights = [24.5] * row_count
    auto_break, bottom_margin = pdf.auto_page_break, pdf.b_margin
    pdf.set_auto_page_break(False, margin=bottom_margin)
    mapped = []
    try:
        # Each spanning cell contributes a minimum height to its covered rows.
        # Allocate extra height to its final row; never split a merged cell.
        for cell in cells:
            width = (cell["bbox"][2] - cell["bbox"][0]) * scale
            text = text_for(by_cell[cell["id"]])
            if width <= 10:
                raise ReviewError("table_column_too_narrow", "A table column is too narrow for readable text. Choose a companion copy or correct its structure.")
            needed = 8
            item = by_cell[cell["id"]]
            if item["disposition"] == "preserve":
                needed += (width - 8) * (item["bbox"][3] - item["bbox"][1]) / (item["bbox"][2] - item["bbox"][0])
            elif text:
                pdf.set_xy(pdf.l_margin, pdf.t_margin)
                try:
                    needed += pdf.multi_cell(width - 8, 16.5, text=text, dry_run=True, output=MethodReturnValue.HEIGHT)
                except ReviewError:
                    raise
                except Exception as exc:
                    raise ReviewError("table_text_fit", "A cell cannot fit at readable body size. Correct its content or layout.") from exc
            end = cell["row"] + cell["row_span"]
            current = sum(heights[cell["row"]:end])
            heights[end - 1] += max(0, needed - current)
        # Connected row-span groups must stay on the same output page.
        start = 0
        groups = []
        while start < row_count:
            end = start + 1
            while True:
                extended = max([end] + [c["row"] + c["row_span"] for c in cells if start <= c["row"] < end])
                if extended == end:
                    break
                end = extended
            groups.append((start, end))
            start = end
        cursor = unit["start_y"]
        for start, end in groups:
            height = sum(heights[start:end])
            if height > pdf.h - pdf.t_margin - bottom_margin:
                raise ReviewError("table_row_overflow", "A table row or merged group is taller than one readable page. Correct the cell text or divide the source table explicitly.")
            if cursor + height > pdf.h - bottom_margin:
                pdf.add_page()
                cursor = pdf.t_margin
            for cell in cells:
                if not start <= cell["row"] < end:
                    continue
                item = by_cell[cell["id"]]
                x = pdf.l_margin + (cell["bbox"][0] - table["bbox"][0]) * scale
                y = cursor + sum(heights[start:cell["row"]])
                width = (cell["bbox"][2] - cell["bbox"][0]) * scale
                cell_height = sum(heights[cell["row"]:cell["row"] + cell["row_span"]])
                pdf.rect(x, y, width, cell_height)
                text = text_for(item)
                if item["disposition"] == "preserve":
                    pdf.image(visual_cell(item, source, source_page), x=x + 4, y=y + 4, w=width - 8)
                elif text:
                    pdf.set_xy(x + 4, y + 4)
                    pdf.multi_cell(width - 8, 16.5, text=text, new_x="LEFT", new_y="NEXT")
                    mapped.append({"source_page": source_page, "region_id": item["id"], "expected": text,
                                   "spans": [{"page": pdf.page_no(), "bbox": [x + 3, y + 3, x + width - 3, y + cell_height - 3]}]})
            cursor += height
        pdf.set_xy(pdf.l_margin, cursor + 8)
        return mapped
    finally:
        pdf.set_auto_page_break(auto_break, margin=bottom_margin)


def render_docx_table(document, unit, text_for, source, source_page):
    from docx.shared import Inches
    table = unit["table"]
    result = document.add_table(rows=table["row_count"], cols=table["column_count"])
    result.style = "Table Grid"
    cells = {(c["row"], c["column"]): c for c in table["cells"]}
    by_cell = {r["cell_id"]: r for r in unit["regions"]}
    for (row, column), cell in sorted(cells.items()):
        target = result.cell(row, column)
        if cell["row_span"] > 1 or cell["column_span"] > 1:
            target = target.merge(result.cell(row + cell["row_span"] - 1, column + cell["column_span"] - 1))
        item = by_cell[cell["id"]]
        target.text = text_for(item)
        if item["disposition"] == "preserve":
            fraction = (cell["bbox"][2] - cell["bbox"][0]) / (table["bbox"][2] - table["bbox"][0])
            target.paragraphs[0].add_run().add_picture(visual_cell(item, source, source_page), width=Inches(6 * fraction))
    return result
