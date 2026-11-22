"""Immutable review exports with shaped Hindi and explicit layout overflow failures."""
import io
import re
import unicodedata
from pathlib import Path

import fitz
from app.core.review import ReviewError
from app.infrastructure.structured_pdf import open_pdf
from app.services.table_export import content_units, render_pdf_table, render_docx_table


# Includes source form-inventory qualification, not only output rendering rules.
# Older snapshots may have cached an empty blocker list for missing form fields.
PDF_VERIFICATION_VERSION = 5


def canonical_text(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def region_text(r):
    if r["disposition"] == "translate":
        return r["translation"] or "[TRANSLATION MISSING]\n" + r["source"]
    if r["disposition"] == "already_target":
        return r["source"]
    if r["disposition"] in ("unreadable", "excluded"):
        return "[UNRESOLVED: " + r["disposition"] + "] " + (r.get("source") or r.get("reason", ""))
    return ""


def text_export(snapshot):
    status = "DRAFT / PARTIAL" if snapshot["blockers"] else "CONTENT REVIEWED"
    lines = [status, snapshot["document"]["filename"], f"Source revision {snapshot['document']['revision']}"]
    for page in snapshot["pages"]:
        lines.append(f"\n--- Original page {page['number']} ---")
        for unit in content_units(page):
            if unit["type"] == "table":
                table = unit["table"]
                lines.append(f"[Table: {table['row_count']} rows x {table['column_count']} columns]")
                for cell in unit["regions"]:
                    lines.append(f"Row {cell['row'] + 1}, column {cell['column'] + 1}, span {cell['row_span']} x {cell['column_span']}: {region_text(cell) or '[empty / visual cell]'}")
                lines.append("[End table]")
                continue
            r = unit["region"]
            text = region_text(r)
            if text:
                lines.append(text)
            elif r["disposition"] == "preserve":
                lines.append(f"[Visual region retained in PDF/DOCX; original page {page['number']}, region {r['order'] + 1}]")
    if snapshot["blockers"]:
        lines.extend(["\nUNRESOLVED ISSUES", *snapshot["blockers"]])
    return "\n".join(lines)


def shaped_document(font_dir, page_format="A4"):
    from fpdf import FPDF
    from fontTools.ttLib import TTFont
    class ReviewPDF(FPDF):
        def _render_styled_text_line(self, text_line, *args, **kwargs):
            # fpdf2 2.8.8 can leave secondary Indic shaping glyphs without a
            # ToUnicode entry. Some readers then extract those glyph IDs as
            # unrelated ASCII. Record the logical text of each rendered line
            # using standard PDF ActualText, without changing the visible glyphs.
            # This hook is version-pinned and covered by multipage Hindi tests.
            for fragment in text_line.fragments:
                cmap = getattr(fragment.font, "cmap", {})
                if any(ord(char) not in cmap and char not in "\n\r\t\u200c\u200d" for char in fragment.characters):
                    raise ReviewError("export_glyph_missing", "The export font cannot display one or more source characters. Install a supporting font or correct the transcription; missing glyphs cannot be approved.")
            original_page = self.page
            start = len(self.pages[self.page].contents)
            result = super()._render_styled_text_line(text_line, *args, **kwargs)
            text = "".join("".join(fragment.characters) for fragment in text_line.fragments)
            if self.page != original_page:
                start = 0
            content = self.pages[self.page].contents
            if text and len(content) > start:
                actual = text.encode("utf-16-be").hex().upper().encode("ascii")
                marker = b"/Span << /ActualText <FEFF" + actual + b"> >> BDC\n"
                self.pages[self.page].contents = content[:start] + marker + content[start:] + b"\nEMC\n"
            return result
    candidates = [Path("C:/Windows/Fonts/Nirmala.ttc"), Path("C:/Windows/Fonts/Nirmala.ttf"),
                  Path("models/fonts/NotoSansDevanagari-Regular.ttf")]
    font_path = next((p for p in candidates if p.is_file()), None)
    if not font_path:
        raise ReviewError("font_missing", "Install a qualified Hindi-capable TrueType font before PDF export.", 503)
    if font_path.suffix.lower() == ".ttc":
        extracted = Path(font_dir) / "review-font.ttf"
        font = TTFont(font_path, fontNumber=0)
        font.save(extracted)
        font.close()
        font_path = extracted
    pdf = ReviewPDF(unit="pt", format=page_format)
    pdf._review_font_dir = font_dir
    pdf._set_min_pdf_version("1.5")
    pdf.add_font("Review", fname=str(font_path))
    # Load tables before releasing the file: failed layout fits must not leave
    # a Windows font handle open and mask the useful overflow error on cleanup.
    pdf.fonts["review"].ttfont.ensureDecompiled()
    pdf.fonts["review"].ttfont.close()
    pdf.set_font("Review", size=11)
    pdf.set_text_shaping(True)
    pdf.set_margins(45, 45, 45)
    pdf.set_auto_page_break(True, margin=45)
    return pdf


def checked_pdf_output(pdf, output=None):
    if any(getattr(font, "missing_glyphs", []) for font in pdf.fonts.values()):
        raise ReviewError("export_glyph_missing", "The export font cannot display one or more source characters. Install a supporting font or correct the transcription; missing glyphs cannot be approved.")
    from app.services.compatible_pdf import compatible_text_pdf
    result = compatible_text_pdf(bytes(pdf.output()), lambda size: shaped_document(pdf._review_font_dir, size))
    if output is not None:
        Path(output).write_bytes(result)
    return result


def readable_pdf(snapshot, source, output):
    import tempfile
    content_map = []
    with tempfile.TemporaryDirectory() as font_dir:
        pdf = shaped_document(font_dir)
        pdf.add_page()
        def paragraph(text, size=11):
            if not text:
                return []
            pdf.set_font("Review", size=size)
            first_page, first_y = pdf.page_no(), pdf.y
            pdf.multi_cell(0, size * 1.5, text=text, new_x="LMARGIN", new_y="NEXT")
            last_page, last_y = pdf.page_no(), pdf.y
            spans = [{"page": number, "bbox": [pdf.l_margin, first_y if number == first_page else pdf.t_margin,
                       pdf.w - pdf.r_margin, last_y if number == last_page else pdf.h - pdf.b_margin]}
                     for number in range(first_page, last_page + 1)]
            pdf.ln(6)
            return spans
        paragraph(snapshot["document"]["filename"], 16)
        paragraph(("DRAFT / PARTIAL - " if snapshot["blockers"] else "Translation review snapshot - ") + f"revision {snapshot['document']['revision']}", 9)
        for page in snapshot["pages"]:
            pdf.start_section(f"Original page {page['number']}", level=0)
            paragraph(f"Original page {page['number']}", 13)
            for unit in content_units(page):
                if unit["type"] == "table":
                    content_map.extend(render_pdf_table(pdf, {**unit, "start_y": pdf.y}, page["number"], region_text, source))
                    continue
                r = unit["region"]
                if r["disposition"] == "preserve":
                    p = source[page["number"] - 1]
                    p.set_rotation(0)
                    box = fitz.Rect(r["bbox"]) & p.rect
                    scale = min(2, 1200 / max(box.width, box.height))
                    image = p.get_pixmap(clip=box, matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
                    width = min(pdf.epw, box.width)
                    height = width * box.height / box.width
                    if height > pdf.eph:
                        width *= pdf.eph / height
                    pdf.image(io.BytesIO(image), w=width)
                    paragraph(f"Visual from original page {page['number']}", 9)
                else:
                    expected = region_text(r)
                    spans = paragraph(expected, 13 if r["kind"] == "heading" else 11)
                    if expected:
                        content_map.append({"source_page": page["number"], "region_id": r["id"],
                                            "expected": expected, "spans": spans})
        if snapshot["blockers"]:
            paragraph("Unresolved issues", 13)
            for issue in snapshot["blockers"]:
                paragraph(issue, 9)
        checked_pdf_output(pdf, output)
    return content_map, []


def faithful_pdf(snapshot, source, output):
    result = fitz.open()
    content_map = []
    for record in snapshot["pages"]:
        page = source[record["number"] - 1]
        page.set_rotation(0)
        if list(page.widgets() or []):
            raise ReviewError("form_layout_review_required", "This form requires logical-field and appearance reconciliation. Use the original plus companion translation until it is qualified.")
        if list(page.annots() or []):
            raise ReviewError("annotation_layout_review_required", "This page contains annotations. Use readable or companion mode until their appearance and content are reconciled.")
        ordered_regions = sorted(record["regions"], key=lambda r: r["order"])
        to_translate = [r for r in ordered_regions if r["disposition"] == "translate"]
        if any(r["kind"] in ("image", "page") or r.get("overlaps") for r in to_translate):
            raise ReviewError("raster_layout_unsupported", "Translated scan regions cannot safely replace their image backgrounds. Use readable or companion mode.")
        for i, r in enumerate(to_translate):
            box = fitz.Rect(r["bbox"])
            if any((box & fitz.Rect(image["bbox"])).get_area() > 0 for image in page.get_image_info()):
                raise ReviewError("image_background_overlap", "This translation overlaps raster imagery. Use companion mode to preserve its background safely.")
            if any((box & fitz.Rect(other["bbox"])).get_area() > 0.5 for other in to_translate[i + 1:]):
                raise ReviewError("overlapping_regions", "Translation regions overlap. Correct their boxes before layout export.")
        for r in to_translate:
            page.add_redact_annot(fitz.Rect(r["bbox"]), fill=False)
        if to_translate:
            page.apply_redactions(images=0, graphics=0, text=0)
        # Only authored text is qualified by compatible_text_pdf. Arbitrary
        # retained source fonts may have the same broken secondary-glyph map
        # even when MuPDF reads their ActualText correctly. Keep the original
        # unchanged and offer a separately authored translation instead.
        if re.search(r"[\u0900-\u097f]", page.get_text()):
            raise ReviewError("retained_text_encoding_unqualified", "This layout retains original Devanagari text whose copy/search encoding is not qualified. Use readable or companion mode; the original stays available.")
        if to_translate:
            import tempfile
            from fpdf.enums import MethodReturnValue
            with tempfile.TemporaryDirectory() as font_dir:
                overlay = shaped_document(font_dir, (page.rect.width, page.rect.height))
                overlay.set_margins(0, 0, 0)
                overlay.set_auto_page_break(False)
                overlay.add_page()
                for r in to_translate:
                    original_size = float(r.get("font_size", 11))
                    floor = min(original_size, 7 if r["kind"] == "footnote" else 9)
                    box = fitz.Rect(r["bbox"])
                    size, fits = original_size, False
                    while size >= floor:
                        overlay.set_font("Review", size=size)
                        overlay.set_xy(box.x0, box.y0)
                        try:
                            height = overlay.multi_cell(box.width, size * 1.25, text=r["translation"], dry_run=True, output=MethodReturnValue.HEIGHT)
                            fits = height <= box.height
                        except ReviewError:
                            raise
                        except Exception:
                            fits = False
                        if fits or size == floor:
                            break
                        size = max(floor, size - 0.5)
                    if not fits:
                        raise ReviewError("layout_overflow", f"Translation does not fit original page {record['number']}, region {r['order'] + 1}. Adjust the region or choose readable mode.")
                    overlay.set_xy(box.x0, box.y0)
                    overlay.multi_cell(box.width, size * 1.25, text=r["translation"], new_x="LEFT", new_y="NEXT")
                with fitz.open(stream=bytes(checked_pdf_output(overlay)), filetype="pdf") as layer:
                    page.show_pdf_page(page.rect, layer, 0)
        # Annotation content is not silently carried into the derivative.
        for annotation in list(page.annots() or []):
            page.delete_annot(annotation)
        result.insert_pdf(source, from_page=record["number"] - 1, to_page=record["number"] - 1, links=False, annots=False)
        # PDF readers commonly follow content-stream order. Appending an
        # overlay after retained paragraphs can invert the approved reading
        # order while all individual boxes still look correct.
        expected_page_text = "\n".join(region_text(r) for r in ordered_regions if region_text(r))
        if canonical_text(result[-1].get_text(sort=False)) != canonical_text(expected_page_text):
            raise ReviewError("layout_reading_order", f"The copied text on original page {record['number']} does not match the approved content and reading order. Use readable or companion mode.")
        for r in record["regions"]:
            expected = region_text(r)
            if expected:
                content_map.append({"source_page": record["number"], "region_id": r["id"], "expected": expected,
                                    "spans": [{"page": result.page_count, "bbox": r["bbox"]}]})
    result.set_metadata({"title": "Translation review snapshot", "subject": "Translated derivative; original signatures do not certify this document."})
    result.save(output, garbage=4, deflate=True)
    result.close()
    return content_map, []


def verify_pdf_content(pdf, content_map):
    """Verify each occurrence at its recorded output location, including page breaks."""
    def canonical(text):
        # Wrapping may add line breaks, but spaces inside approved text remain
        # meaningful: "not able" must never match "notable". An allocation
        # must also contain exactly its approved content, not a substring of
        # a longer amount or duplicated clause.
        return canonical_text(text)
    missing = []
    for item in content_map:
        parts = []
        for span in item["spans"]:
            if not 1 <= span["page"] <= pdf.page_count:
                continue
            # Bounding boxes are the actual text allocation, not a whole-document search.
            # Small shaping overshoots are included without reaching the next paragraph.
            box = fitz.Rect(span["bbox"]) + (-1, -1, 1, 1)
            parts.append(pdf[span["page"] - 1].get_text(clip=box, sort=False))
        actual = "\n".join(parts)
        if "\ufffd" in actual or canonical(item["expected"]) != canonical(actual):
            missing.append(f"page {item['source_page']}, region {item['region_id']}")
    if missing:
        raise ReviewError("export_text_mismatch", "Export text verification failed for " + "; ".join(missing[:10]) + ". The export is not eligible for final approval.")


def build_export(snapshot, source_path, output_path, format, mode, password=""):
    output = Path(output_path)
    text = text_export(snapshot)
    with open_pdf(source_path, password) as source:
        if source.xref_get_key(source.pdf_catalog(), "AcroForm/XFA")[0] != "null":
            raise ReviewError("xfa_unsupported", "Dynamic XFA forms cannot be exported safely. Obtain a static printable source from its author.")
        if format == "txt":
            output.write_text(text, encoding="utf-8-sig")
            return {"path": str(output), "page_count": 0, "render_verified": False, "text_verified": True}
        if format == "docx":
            from docx import Document
            from docx.shared import Inches, Pt
            document = Document()
            document.styles["Normal"].font.name = "Nirmala UI"
            document.styles["Normal"].font.size = Pt(11)
            document.add_heading(snapshot["document"]["filename"], 0)
            document.add_paragraph("DRAFT / PARTIAL" if snapshot["blockers"] else "Content reviewed; Word pagination requires separate visual review.")
            for page in snapshot["pages"]:
                document.add_heading(f"Original page {page['number']}", 1)
                for unit in content_units(page):
                    if unit["type"] == "table":
                        render_docx_table(document, unit, region_text, source, page["number"])
                        continue
                    r = unit["region"]
                    if r["disposition"] == "preserve":
                        p = source[page["number"] - 1]
                        p.set_rotation(0)
                        box = fitz.Rect(r["bbox"]) & p.rect
                        scale = min(2, 1200 / max(box.width, box.height))
                        png = p.get_pixmap(clip=box, matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
                        document.add_picture(io.BytesIO(png), width=Inches(min(6, box.width / 72)))
                    else:
                        document.add_paragraph(region_text(r))
            for issue in snapshot["blockers"]:
                document.add_paragraph(issue)
            document.save(output)
            return {"path": str(output), "page_count": 0, "render_verified": False, "text_verified": True}
        if mode == "faithful":
            if snapshot["blockers"]:
                raise ReviewError("content_unreviewed", "Layout-preserving export requires all content and completeness reviews first.")
            content_map, compatibility_issues = faithful_pdf(snapshot, source, output)
        else:
            content_map, compatibility_issues = readable_pdf(snapshot, source, output)
            if mode == "companion":
                for item in content_map:
                    for span in item["spans"]:
                        span["page"] += source.page_count
                with fitz.open(output) as translation:
                    combined = fitz.open()
                    # Rasterized source pages preserve visible form appearances without active widgets/scripts.
                    for original in source:
                        p = combined.new_page(width=original.rect.width, height=original.rect.height)
                        scale = min(2, 1800 / max(original.rect.width, original.rect.height))
                        p.insert_image(p.rect, pixmap=original.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False))
                    combined.insert_pdf(translation)
                    temp = output.with_suffix(".combined.pdf")
                    combined.save(temp)
                    combined.close()
                temp.replace(output)
    with fitz.open(output) as pdf:
        # Independent content checks do not substitute for human visual approval.
        extracted = "\n".join(page.get_text() for page in pdf)
        if "\ufffd" in extracted:
            raise ReviewError("export_glyph_error", "Export contains replacement characters. Review fonts and source transcription.")
        verify_pdf_content(pdf, content_map)
        for page in pdf:
            page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), alpha=False)
        return {"path": str(output), "page_count": pdf.page_count, "render_verified": True,
                "text_verified": bool(extracted.strip()), "layout_review_required": True, "content_map": content_map,
                "compatibility_issues": compatibility_issues, "text_encoding": "verified-vector-and-logical-text-v1",
                "text_encoding_scope": "app-authored text; original companion pages are visual copies",
                "verification_version": PDF_VERIFICATION_VERSION}
