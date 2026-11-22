"""Preserve shaped vector appearance with an independently verified logical text layer.

Some readers ignore ActualText and misdecode secondary Indic glyphs. Only pages
authored by this application enter this transformation; original PDFs do not.
"""
import re
import unicodedata
import fitz
from PIL import Image, ImageChops, ImageFilter
from fpdf.enums import TextMode
from app.core.review import ReviewError


def verify_appearance(source, result):
    if abs(source.rect.width - result.rect.width) > 0.02 or abs(source.rect.height - result.rect.height) > 0.02:
        raise ReviewError("outline_dimensions", "The compatible text layer changed page dimensions.")
    scale = min(2, 1800 / max(source.rect.width, source.rect.height))
    images = []
    for page in (source, result):
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    if images[0].size != images[1].size:
        raise ReviewError("outline_dimensions", "The compatible text layer changed rendered dimensions.")
    # Outlines do not use TrueType grid hinting. Compare ink geometry within
    # one pixel, plus channel colors on flat areas (tolerance 8/255). This
    # retains color checks without treating edge antialiasing as lost text.
    # Both directions are checked: adding content also fails verification.
    for actual, expected in (images, list(reversed(images))):
        ink = actual.convert("L").point(lambda v: 255 if v < 224 else 0)
        reference = expected.convert("L").point(lambda v: 255 if v < 224 else 0)
        missing = ImageChops.subtract(ink, reference.filter(ImageFilter.MaxFilter(3)))
        if sum(missing.histogram()[1:]):
            raise ReviewError("outline_appearance", "Text compatibility conversion changed the rendered appearance. No final export was created.")
        minimum = expected.filter(ImageFilter.MinFilter(3))
        maximum = expected.filter(ImageFilter.MaxFilter(3))
        ranges = ImageChops.subtract(maximum, minimum)
        for a, b, neighborhood in zip(actual.split(), expected.split(), ranges.split()):
            flat = neighborhood.point(lambda v: 255 if v <= 8 else 0)
            difference = ImageChops.difference(a, b).point(lambda v: 255 if v > 8 else 0)
            if sum(ImageChops.multiply(flat, difference).histogram()[1:]):
                raise ReviewError("outline_appearance", "Text compatibility conversion changed the rendered appearance. No final export was created.")


def compatible_text_pdf(data, document_factory):
    result = fitz.open()
    try:
        with fitz.open(stream=data, filetype="pdf") as source:
            for original in source:
                text = original.get_text()
                if not re.search(r"[\u0900-\u097f]", text):
                    result.insert_pdf(source, from_page=original.number, to_page=original.number)
                    continue
                svg = original.get_svg_image(text_as_path=True).encode("utf-8")
                if len(svg) > 32 * 1024**2:
                    raise ReviewError("outline_complexity", "This page exceeds the bounded vector export size. Split the export or review the layout manually.")
                with fitz.open(stream=svg, filetype="svg") as drawing:
                    vector_data = drawing.convert_to_pdf()
                with fitz.open(stream=vector_data, filetype="pdf") as visual:
                    if visual.page_count != 1 or visual[0].get_text().strip():
                        raise ReviewError("outline_text_leak", "The visual layer unexpectedly retained selectable glyphs. Export was blocked.")
                    layer = document_factory((original.rect.width, original.rect.height))
                    layer.set_text_shaping(False)
                    layer.text_mode = TextMode.INVISIBLE
                    layer.add_page()
                    for block in original.get_text("dict")["blocks"]:
                        for line in block.get("lines", []):
                            spans = line["spans"]
                            value = "".join(span["text"] for span in spans)
                            if not value:
                                continue
                            if line.get("dir") != (1.0, 0.0):
                                raise ReviewError("outline_direction", "Rotated text needs separate text-layer qualification.")
                            if any(ord(c) not in layer.fonts["review"].cmap for c in value):
                                raise ReviewError("outline_glyph", "A logical text character is unsupported by the export font.")
                            layer.set_font("Review", size=spans[0]["size"])
                            layer.set_stretching(100)
                            width = layer.get_string_width(value)
                            if width <= 0:
                                raise ReviewError("outline_text_width", "A text line has no measurable width.")
                            layer.set_stretching(100 * (line["bbox"][2] - line["bbox"][0]) / width)
                            layer.text(line["bbox"][0], spans[0]["origin"][1], value)
                    with fitz.open(stream=bytes(layer.output()), filetype="pdf") as logical:
                        visual[0].show_pdf_page(visual[0].rect, logical, 0)
                    canonical = lambda value: unicodedata.normalize("NFC", value).split()
                    if canonical(text) != canonical(visual[0].get_text()):
                        raise ReviewError("outline_text_mismatch", "The logical text layer changed the generated content. Export was blocked.")
                    verify_appearance(original, visual[0])
                    result.insert_pdf(visual)
            result.set_metadata(source.metadata)
            if source.get_toc():
                result.set_toc(source.get_toc())
            return result.tobytes(garbage=4, deflate=True)
    finally:
        result.close()
