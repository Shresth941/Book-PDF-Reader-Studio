"""Region-level extraction with raw evidence. Used only in bounded processes."""
from pathlib import Path
import hashlib
import math
import shutil
from statistics import median
from uuid import uuid4

import fitz
from app.core.review import ReviewError, language_hint


def open_pdf(path, password=""):
    try:
        pdf = fitz.open(path)
    except Exception as exc:
        raise ReviewError("invalid_pdf", "The PDF cannot be opened. Its original bytes have been retained.") from exc
    if pdf.needs_pass and not pdf.authenticate(password):
        pdf.close()
        raise ReviewError("password_required", "Enter the PDF password to inspect this document.", 423)
    return pdf


def inspect_pdf(path, max_pages=1000, password=""):
    try:
        pdf = open_pdf(path, password)
    except ReviewError as exc:
        if exc.code == "password_required":
            return {"status": "password_required", "page_count": 0, "message": exc.message, "pages": []}
        raise
    with pdf:
        if pdf.xref_get_key(pdf.pdf_catalog(), "AcroForm/XFA")[0] != "null":
            raise ReviewError("xfa_unsupported", "This PDF contains XFA data or dynamic forms. Their rendering and bindings are not supported. Keep the original and import a static printable copy from its author.")
        if not 0 < pdf.page_count <= max_pages:
            raise ReviewError("page_limit", f"Choose a document with 1 to {max_pages} pages. The original has been retained.")
        pages = []
        for i in range(pdf.page_count):
            page = pdf[i]
            rect = page.rect
            if not all(math.isfinite(v) for v in rect) or rect.width <= 0 or rect.height <= 0:
                raise ReviewError("invalid_page_size", f"Page {i + 1} has invalid dimensions.")
            # All extracted boxes use the unrotated visible CropBox coordinate space.
            rotation = page.rotation
            page.set_rotation(0)
            pages.append({"number": i + 1, "width": page.rect.width, "height": page.rect.height,
                          "rotation": rotation, "cropbox": list(page.cropbox), "mediabox": list(page.mediabox),
                          "rotation_matrix": list(fitz.Matrix(rotation)),
                          "fonts": [str(f[3]) for f in page.get_fonts()],
                          "image_count": len(page.get_images()),
                          "annotations": [a.type[1] for a in (page.annots() or [])],
                          "forms": [{"name": w.field_name, "value": str(w.field_value), "type": w.field_type_string,
                                     "bbox": list(w.rect)} for w in (page.widgets() or [])]})
        return {"status": "ready", "page_count": pdf.page_count, "pages": pages,
                "attachments": pdf.embfile_names(), "repaired_by_parser": pdf.is_repaired,
                "signed": bool(pdf.get_sigflags()), "form_review_required": any(p["forms"] for p in pages)}


def tesseract_path():
    return shutil.which("tesseract") or next((str(p) for p in
        (Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"), Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")) if p.is_file()), None)


def region(kind, box, text="", **kwargs):
    return {"id": str(uuid4()), "order": 0, "kind": kind, "bbox": list(box), "raw": text,
            "source": text, "translation": "", "source_language": "uncertain", "language_hint": language_hint(text),
            "disposition": "translate" if text.strip() else "unreadable", "contains_text": True,
            "method": "native", "alternatives": [], "evidence": [], "reason": "",
            "source_approved": False, "translation_approved": False, "acknowledged": {}, **kwargs}


def form_regions(forms, rect):
    regions = []
    for field in forms["widgets"]:
        value = field.get("stored_value")
        raw = "" if value is None else str(value)
        box = fitz.Rect(field["bbox"]) & rect
        notes = [f"Stored field '{field['field_name']}' value: {raw}. Compare its original appearance before choosing a transcription."]
        notes.extend(d["message"] for d in field["discrepancies"])
        notes.extend(field["warnings"])
        if box.is_empty:
            box = rect
            notes.append("This field has no visible rectangle on the page. Its recorded field bounds are retained in the form evidence.")
        regions.append(region("manual", box, raw, method="form-field", disposition="unreadable",
            evidence=notes, form_evidence=field, form_review_note=""))
    return regions


def verify_original(path, document_sha256):
    with Path(path).open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != document_sha256:
        raise ReviewError("original_changed", "The original PDF fingerprint changed. No form evidence was replaced.", 409)
    return {"document_sha256": actual}


def inspect_page_forms(path, number, document_sha256, password=""):
    """Refresh evidence only, bound to original bytes; never rerun OCR/extraction."""
    verify_original(path, document_sha256)
    with open_pdf(path, password) as pdf:
        if not 1 <= number <= pdf.page_count:
            raise ReviewError("page_not_found", "Page not found.", 404)
        page = pdf[number - 1]
        page.set_rotation(0)
        from app.infrastructure.form_evidence import inspect_form_evidence
        forms = inspect_form_evidence(pdf, page)
        if forms["xfa"]["present"]:
            raise ReviewError("xfa_unsupported", forms["xfa"]["message"])
        if forms.get("inventory_complete") is not True:
            raise ReviewError("form_inventory_incomplete", "The complete field inventory could not be inspected. Saved evidence and edits are unchanged; obtain a static printable copy if retrying cannot resolve it.")
        result = {"document_sha256": document_sha256, "number": number, "width": page.rect.width,
                  "height": page.rect.height, "form_evidence": forms, "regions": form_regions(forms, page.rect)}
    verify_original(path, document_sha256)
    return result


def ocr_region(page, box, alternate=False, sparse=False):
    import pytesseract
    from PIL import Image, ImageOps
    executable = tesseract_path()
    if not executable:
        return {"method": "tesseract", "text": "", "error": "Install Tesseract with hin and eng language data to enable local OCR."}
    pytesseract.pytesseract.tesseract_cmd = executable
    languages = set(pytesseract.get_languages(config=""))
    if not {"eng", "hin"} <= languages:
        return {"method": "tesseract", "text": "", "error": "Tesseract hin and eng language data are both required."}
    clip = fitz.Rect(box) & page.rect
    # Tiles limit decoded bitmap size without silently reducing source resolution.
    scale = 300 / 72
    tile_side = 2000 / scale
    text, evidence = [], []
    for top in range(math.ceil(clip.height / tile_side)):
        for left in range(math.ceil(clip.width / tile_side)):
            tile = fitz.Rect(clip.x0 + left * tile_side, clip.y0 + top * tile_side,
                             min(clip.x1, clip.x0 + (left + 1) * tile_side), min(clip.y1, clip.y0 + (top + 1) * tile_side))
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=tile, colorspace=fitz.csRGB, alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            if alternate:
                image = ImageOps.autocontrast(ImageOps.grayscale(image))
            segmentation = 11 if sparse else 6 if alternate else 3
            data = pytesseract.image_to_data(image, lang="hin+eng", config=f"--psm {segmentation}", output_type=pytesseract.Output.DICT, timeout=25)
            lines = {}
            for i, word in enumerate(data["text"]):
                if not word.strip():
                    continue
                key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
                lines.setdefault(key, []).append(word)
                evidence.append({"text": word, "confidence": float(data["conf"][i]),
                                 "bbox": [tile.x0 + data["left"][i] / scale, tile.y0 + data["top"][i] / scale,
                                          tile.x0 + (data["left"][i] + data["width"][i]) / scale,
                                          tile.y0 + (data["top"][i] + data["height"][i]) / scale]})
            text.extend(" ".join(words) for words in lines.values())
    return {"method": "tesseract-alternate" if alternate else "tesseract-original", "text": "\n".join(text), "words": evidence,
            "warning": "Check tile boundaries, column order and faint marks against the full page."}


def legacy_recognition_box(block):
    """Bad legacy font ascenders can include the preceding paragraph in a crop."""
    box = fitz.Rect()
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            bounds = fitz.Rect(span["bbox"])
            size = span["size"]
            if bounds.height > size * 1.5:
                baseline = span["origin"][1]
                bounds.y0, bounds.y1 = baseline - size, baseline + size * .25
            box |= bounds
    # Leave breathing room for matras and glyphs that exceed advance widths.
    return box + (-4, -2, 4, 2)


def text_layout(block):
    """Baseline-based display bounds avoid inflated ascenders in legacy fonts."""
    lines = [line for line in block.get("lines", []) if line.get("spans")]
    spans = [s for line in lines for s in line["spans"]]
    size = median(s["size"] for s in spans)
    baselines = [line["spans"][0]["origin"][1] for line in lines]
    gaps = [b - a for a, b in zip(baselines, baselines[1:]) if b - a > size * .5]
    leading = median(gaps) if gaps else size * 1.35
    x0, _, x1, _ = block["bbox"]
    top = min(s["origin"][1] - s["size"] * .9 for s in spans)
    bottom = max(s["origin"][1] + s["size"] * .45 for s in spans)
    centers = [(line["bbox"][0] + line["bbox"][2]) / 2 for line in lines]
    centered = len(lines) > 1 and max(centers) - min(centers) < size * .6
    return {"bbox": [x0, top, x1, bottom], "font_size": size, "line_height": leading,
            "lines": [{"x": line["bbox"][0], "y": line["spans"][0]["origin"][1] - size * .9,
                       "width": line["bbox"][2] - line["bbox"][0]} for line in lines],
            "align": "center" if centered else "left",
            "bold": all("bold" in s.get("font", "").lower() for s in spans),
            "italic": all(any(t in s.get("font", "").lower() for t in ("italic", "oblique")) for s in spans)}


def extract_page(path, number, password="", retry=False, document_sha256=None):
    if document_sha256:
        verify_original(path, document_sha256)
    with open_pdf(path, password) as pdf:
        page = pdf[number - 1]
        page.set_rotation(0)
        if page.rect.width * page.rect.height > 150_000_000:
            raise ReviewError("page_too_large", "This page requires manual region processing because of its dimensions.")
        blocks = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES, sort=True)["blocks"]
        regions = []
        for block in blocks:
            if block.get("type") != 0:
                continue
            text = "\n".join("".join(s.get("text", "") for s in line["spans"]) for line in block.get("lines", []))
            if not text.strip():
                continue
            fonts = [s.get("font", "") for line in block.get("lines", []) for s in line["spans"]]
            # XpressChanak (including subset/bold/italic names) uses the same
            # legacy glyph encoding family as Chanakya, not Unicode Hindi.
            legacy = any(h in f.casefold() for f in fonts for h in ("shiva", "kruti", "chanak", "devlys"))
            item = region("paragraph", block["bbox"], text, fonts=fonts,
                          font_size=min((s["size"] for line in block["lines"] for s in line["spans"]), default=11),
                          text_layout=text_layout(block))
            if legacy or "\ufffd" in text:
                from app.infrastructure.legacy_hindi import decode_block
                decoded = decode_block(block) if legacy and "\ufffd" not in text else None
                if decoded is not None:
                    item["source"] = decoded
                    item["language_hint"] = language_hint(decoded)
                    item["alternatives"] = [{"method": "xpress-chanak-v1", "text": decoded}]
                    item["evidence"].append("XpressChanak glyphs decoded directly to Unicode; original encoded text retained. Check against the original before approval.")
                else:
                    box = legacy_recognition_box(block) if legacy else block["bbox"]
                    item["alternatives"] = [ocr_region(page, box, retry, sparse=legacy and item["font_size"] >= 24)]
                    item["source"] = ""
                    item["disposition"] = "unreadable"
                    item["evidence"].append("Native encoding is unreliable; compare OCR alternatives and transcribe the source.")
            item["text_layout"]["source_text"] = item["source"]
            regions.append(item)
        for image in page.get_image_info():
            box = list(fitz.Rect(image["bbox"]) & page.rect)
            if fitz.Rect(box).is_empty:
                continue
            candidate = ocr_region(page, box, retry)
            # Image and native text may overlap (hidden OCR). Keep both with an explicit
            # overlap warning instead of choosing an unverified text layer.
            overlaps = [r["id"] for r in regions if (fitz.Rect(r["bbox"]) & fitz.Rect(box)).get_area() > 0]
            regions.append(region("image", box, method="image-ocr", alternatives=[candidate],
                                  evidence=["Verify visible image text and reconcile overlapping native regions."], overlaps=overlaps))
        for annotation in page.annots() or []:
            box = fitz.Rect(annotation.rect) & page.rect
            if not box.is_empty:
                text = annotation.info.get("content", "")
                regions.append(region("manual", box, text, method="annotation", disposition="unreadable",
                    evidence=["Annotation/comment content requires explicit transcription or a documented visual disposition. It is not automatically part of body text."]))
        from app.infrastructure.form_evidence import inspect_form_evidence
        forms = inspect_form_evidence(pdf, page)
        if forms["xfa"]["present"]:
            raise ReviewError("xfa_unsupported", forms["xfa"]["message"])
        regions.extend(form_regions(forms, page.rect))
        if not regions:
            regions.append(region("page", page.rect, method="manual", evidence=["Confirm whether this page is blank or contains undetected content."]))
        for index, item in enumerate(regions):
            item["order"] = index
        from app.infrastructure.table_structure import extract_table_proposals
        tables = extract_table_proposals(page, number)
        result = {"number": number, "width": page.rect.width, "height": page.rect.height,
                "status": "extracted", "regions": regions, "coverage_approved": False, "error": "",
                "table_proposals": tables, "form_evidence": forms}
    if document_sha256:
        verify_original(path, document_sha256)
    return result


def render_page(path, number, output_path, password="", max_dimension=1600):
    with open_pdf(path, password) as pdf:
        page = pdf[number - 1]
        page.set_rotation(0)
        scale = min(2, max_dimension / max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
        pix.save(output_path)
        return {"path": output_path, "width": pix.width, "height": pix.height}
