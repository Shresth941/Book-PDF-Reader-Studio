import io
import logging
import re
import shutil
from threading import Lock
from pathlib import Path

import fitz

logger = logging.getLogger(__name__)

try:
    import pytesseract
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency protection
    pytesseract = None
    Image = None

try:
    from rapidocr import RapidOCR
except Exception:  # pragma: no cover - optional dependency protection
    logger.info("Optional legacy RapidOCR is absent; the local review pipeline uses Tesseract.")
    RapidOCR = None

_tesseract_available = False
if pytesseract is not None:
    tesseract_path = shutil.which("tesseract")
    if tesseract_path is None:
        for candidate in (
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        ):
            if candidate.is_file():
                tesseract_path = str(candidate)
                break
    if tesseract_path and Path(tesseract_path).is_file():
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
        _tesseract_available = True
    elif tesseract_path:
        _tesseract_available = True

_rapid_ocr_engine = None
_rapid_ocr_failed = RapidOCR is None
_rapid_ocr_lock = Lock()


class PyMuPdfReader:
    # These fonts often contain Hindi glyphs encoded with a legacy keyboard layout
    # (rather than Unicode). PDF libraries then return text such as ``?kks\"k.kk``.
    # The name check is deliberately narrow: a regular font that happens to contain
    # punctuation must never have its text removed.
    _legacy_font_hints = ("shiva", "kruti", "chanak", "devlys")
    _windows_punctuation = str.maketrans({
        "\x91": "'",
        "\x92": "'",
        "\x93": '"',
        "\x94": '"',
        "\x96": "-",
        "\x97": "-",
    })
    _roman_numeral_pattern = re.compile(
        r"^M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})$",
        re.IGNORECASE,
    )

    @staticmethod
    def _is_noisy_or_missing_text(text: str) -> bool:
        cleaned = text.strip()
        if not cleaned:
            return True
        # If the text has letters/digits and readable words, don't trigger OCR even if short
        letters_and_digits = sum(ch.isalnum() for ch in cleaned)
        if letters_and_digits == 0:
            return True
        weird_char_ratio = sum(ch.isalnum() or ch.isspace() or ch in ".,;:'\"()[]{}!?/-–—\n" for ch in cleaned) / max(len(cleaned), 1)
        if len(cleaned) > 20 and weird_char_ratio < 0.4:
            return True
        if cleaned.count("\ufffd") > 5:
            return True
        return False

    @staticmethod
    def _is_image_dominant_page(page: fitz.Page) -> bool:
        """Return whether one or more images cover most of the visible page.

        Scanned PDFs often have a low-quality, invisible OCR layer.  It is not
        empty, so a simple ``get_text`` check accepts it even when the visible
        page is an image.  Measuring the image coverage lets us restrict the
        more expensive OCR pass to pages where it can actually improve the
        result.
        """
        page_area = page.rect.width * page.rect.height
        if page_area <= 0:
            return False
        try:
            image_area = sum(
                max(0.0, info["bbox"][2] - info["bbox"][0])
                * max(0.0, info["bbox"][3] - info["bbox"][1])
                for info in page.get_image_info()
                if info.get("bbox")
            )
        except Exception:
            return False
        return image_area / page_area >= 0.75

    @classmethod
    def _needs_scan_ocr(cls, page: fitz.Page, text: str) -> bool:
        if cls._is_noisy_or_missing_text(text):
            return True
        if not cls._is_image_dominant_page(page):
            return False

        # A trustworthy text layer rarely consists of many isolated one- and
        # two-character fragments.  This catches broken OCR layers such as
        # ``sity / ROWAN / eats`` while leaving ordinary scanned pages with a
        # usable text layer on the faster native extraction path.
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 6:
            return False
        short_fragments = sum(
            len(re.sub(r"[^\w]", "", line, flags=re.UNICODE)) <= 3
            for line in lines
        )
        return short_fragments > max(3, len(lines) * 0.35)

    @staticmethod
    def _clean_text(text: str) -> str:
        text = text.replace("\x00", "").translate(PyMuPdfReader._windows_punctuation)
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n +", "\n", text)
        text = re.sub(r" +\n", "\n", text)
        return text.strip()

    @staticmethod
    def _render_ocr_image(page: fitz.Page):
        """Render a page for OCR without an unnecessary PNG encode/decode.

        OCR is only used for a scanned or damaged text layer, so the rendered
        bitmap is often the slowest local step.  PyMuPDF already provides raw
        pixels; passing them directly to Pillow avoids compressing a large
        300-DPI page to PNG and immediately reading it back again.  The PNG
        fallback keeps this compatible with unusual colour spaces and older
        PyMuPDF builds.
        """
        if Image is None:
            return None
        pix = page.get_pixmap(dpi=300, alpha=False)
        try:
            components = int(pix.n)
            mode = {1: "L", 3: "RGB", 4: "RGBA"}.get(components)
            if mode and pix.width > 0 and pix.height > 0:
                return Image.frombytes(mode, (pix.width, pix.height), pix.samples)
        except (AttributeError, TypeError, ValueError):
            pass
        return Image.open(io.BytesIO(pix.tobytes("png")))

    @classmethod
    def _legacy_encoded_fonts(cls, text_dict: dict) -> set[str]:
        """Return font names whose text cannot be extracted as Unicode.

        Some bilingual government forms use legacy Hindi fonts. Their visible
        Hindi is correct, but the PDF has no usable Unicode character map for
        that font. Filtering spans by font lets us retain the document's native
        English text exactly, instead of showing the keyboard-encoding garbage.
        """
        font_samples: dict[str, list[str]] = {}
        for block in text_dict.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    font = str(span.get("font", ""))
                    text = str(span.get("text", ""))
                    if text.strip():
                        font_samples.setdefault(font, []).append(text)

        legacy_fonts: set[str] = set()
        for font, samples in font_samples.items():
            font_name = font.casefold()
            combined = " ".join(samples)
            has_legacy_name = any(hint in font_name for hint in cls._legacy_font_hints)
            # This secondary check covers unnamed legacy fonts while requiring a
            # strong signal, so normal English forms with occasional punctuation
            # continue to use their embedded text unchanged.
            malformed_tokens = re.findall(r"[A-Za-z][A-Za-z0-9]*[;{}\[\]=][A-Za-z0-9;{}\[\]=]*", combined)
            has_strong_encoding_signal = len(malformed_tokens) >= 12
            if has_legacy_name or has_strong_encoding_signal:
                legacy_fonts.add(font)
        return legacy_fonts

    @staticmethod
    def _field_number_prefix(text: str) -> str:
        """Keep a leading form-field number when its Hindi label is omitted."""
        match = re.match(r"\s*(\d{1,2}\s*[-.)])", text)
        return match.group(1).replace(" ", "") if match else ""

    def _extract_ordered_text(self, page: fitz.Page) -> str:
        """Preserve raw legacy spans; the review pipeline flags unreliable encoding."""
        try:
            blocks = []
            for block in page.get_text("dict", sort=True).get("blocks", []):
                lines = ["".join(str(span.get("text", "")) for span in line.get("spans", [])) for line in block.get("lines", [])]
                if lines:
                    blocks.append("\n".join(lines))
            return self._clean_text("\n\n".join(blocks))
        except Exception:
            return self._clean_text(page.get_text())

    @staticmethod
    def _format_ocr_line(text: str) -> str:
        """Tidy OCR token spacing without changing the words that were read."""
        text = re.sub(r"\s+", " ", text).strip()

        # A centred roman-numeral folio is commonly split into three OCR
        # fragments: ``(``, ``vii`` and ``)``.  Keep the visible book-style
        # spacing for this very specific header, while ordinary parenthetical
        # text remains compact below.
        folio = re.fullmatch(r"\(\s*([ivxlcdm]+)\s*\)", text, flags=re.IGNORECASE)
        if folio:
            return f"( {folio.group(1)} )"

        text = re.sub(r"\(\s+", "(", text)
        text = re.sub(r"\s+\)", ")", text)
        text = re.sub(r"\[\s+", "[", text)
        text = re.sub(r"\s+\]", "]", text)
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        return text

    @classmethod
    def _clean_ocr_lines(cls, lines: list[str]) -> str:
        """Keep uncertainty, underscores, case and line-end hyphens for review."""
        return "\n".join(line.rstrip("\r\n") for line in lines).strip()

    @staticmethod
    def _is_contents_style(lines: list[str]) -> bool:
        """Identify a contents/index page without misclassifying normal prose."""
        numbered_rows = sum(
            bool(re.match(r"^\s*\d{1,3}[.)]\s+", line))
            for line in lines
        )
        page_references = sum(
            bool(re.fullmatch(r"\s*(?:\d{1,3}|[IVXLCDM]+)\s*[.,;:]?\s*", line, flags=re.IGNORECASE))
            for line in lines
        )
        return numbered_rows >= 4 and page_references >= 4

    @classmethod
    def _rapid_ocr_page(cls, page: fitz.Page) -> str:
        """Use the local ONNX OCR model for scanned prose when available."""
        global _rapid_ocr_engine, _rapid_ocr_failed
        if _rapid_ocr_failed or RapidOCR is None or Image is None:
            return ""
        try:
            # 300 DPI matches the source image resolution in many older scans
            # and avoids an expensive upscale with no new visual information.
            image = cls._render_ocr_image(page)
            if image is None:
                return ""
            with _rapid_ocr_lock:
                if _rapid_ocr_engine is None:
                    _rapid_ocr_engine = RapidOCR()
                output = _rapid_ocr_engine(image)

            texts = list(getattr(output, "txts", ()) or ())
            scores = list(getattr(output, "scores", ()) or ())
            lines: list[str] = []
            for index, text in enumerate(texts):
                line = str(text).strip()
                try:
                    score = float(scores[index])
                except (IndexError, TypeError, ValueError):
                    score = 1.0
                # Discard only isolated, low-confidence scan noise. Short
                # legitimate labels such as Roman numerals are retained.
                if line:
                    lines.append(line)
            return cls._clean_ocr_lines(lines) if lines else ""
        except Exception:
            logger.warning("RapidOCR failed while processing a scanned page.", exc_info=True)
            return ""

    @classmethod
    def _is_roman_numeral(cls, text: str) -> bool:
        """Recognise a conventional Roman numeral without changing its case."""
        candidate = text.strip().strip(".,;:()[]")
        return bool(candidate and cls._roman_numeral_pattern.fullmatch(candidate))

    @staticmethod
    def _recover_right_aligned_page_number(word: str, left: float, right_edge: float) -> str:
        """Recover only an OCR-confused contents-page number near the right edge."""
        if left < right_edge * 0.83:
            return ""
        digits = word.translate(str.maketrans({
            "I": "1", "l": "1", "i": "1", "t": "1", "T": "1",
            "O": "0", "o": "0", "S": "5", "s": "5", "B": "8",
        }))
        digits = re.sub(r"\D", "", digits)
        return digits if 2 <= len(digits) <= 3 else ""

    @classmethod
    def _is_left_contents_number_fragment(cls, word: str, left: float, right_edge: float) -> bool:
        """Keep a faint item label so a verified contents sequence can repair it."""
        return left <= right_edge * 0.3 and (
            bool(re.fullmatch(r"\d[A-Za-z]", word)) or cls._is_roman_numeral(word)
        )

    @staticmethod
    def _repair_contents_numbering(lines: list[str]) -> list[str]:
        """Numbering changes require review; preserve nonconsecutive labels."""
        return list(lines)

    @classmethod
    def _rebuild_positioned_ocr_lines(cls, data: dict) -> str:
        """Rebuild visual rows from OCR word positions.

        ``--psm 11`` is intentionally tolerant of sparse scans, but it often
        treats a contents row's number, label and page reference as separate
        lines.  The coordinates in ``image_to_data`` are more reliable than
        those line labels, so merge only bands that contain an isolated layout
        fragment (for example ``43.``, ``(``, or a right-aligned page number).
        Full text blocks are otherwise kept in Tesseract's original order so
        that two-column pages do not get flattened into one line.
        """
        word_count = len(data.get("text", []))
        source_lines: dict[tuple[int, int, int], list[dict[str, float | int | str]]] = {}

        for index in range(word_count):
            word = str(data["text"][index]).strip()
            if not word:
                continue
            try:
                confidence = float(data["conf"][index])
            except (IndexError, TypeError, ValueError):
                continue
            if confidence < 0:
                continue

            try:
                block = int(data["block_num"][index])
                paragraph = int(data["par_num"][index])
                line = int(data["line_num"][index])
                left = float(data["left"][index])
                top = float(data["top"][index])
                width = max(1.0, float(data["width"][index]))
                height = max(1.0, float(data["height"][index]))
            except (IndexError, KeyError, TypeError, ValueError):
                continue

            source_lines.setdefault((block, paragraph, line), []).append({
                "word": word,
                "confidence": confidence,
                "left": left,
                "top": top,
                "width": width,
                "height": height,
                "index": index,
            })

        if not source_lines:
            return ""

        right_edge = max(
            float(word["left"]) + float(word["width"])
            for words in source_lines.values()
            for word in words
        )
        segments: list[dict[str, float | int | str]] = []
        for words in source_lines.values():
            # Do not discard an otherwise clear page number merely because a
            # faint dotted leader beside it lowered the old line-average score.
            kept_words = []
            for word in words:
                confidence = float(word["confidence"])
                if confidence >= 42:
                    kept_words.append(word)
                    continue
                if cls._is_roman_numeral(str(word["word"])) and (
                    float(word["left"]) <= right_edge * 0.3
                    or float(word["left"]) >= right_edge * 0.83
                ):
                    kept_words.append(word)
                    continue
                recovered_number = cls._recover_right_aligned_page_number(
                    str(word["word"]), float(word["left"]), right_edge,
                )
                if recovered_number:
                    kept_words.append({**word, "word": recovered_number})
                    continue
                if cls._is_left_contents_number_fragment(
                    str(word["word"]), float(word["left"]), right_edge,
                ):
                    kept_words.append(word)
            if not kept_words:
                continue

            kept_words.sort(key=lambda word: (float(word["left"]), int(word["index"])))
            left = min(float(word["left"]) for word in kept_words)
            top = min(float(word["top"]) for word in kept_words)
            bottom = max(float(word["top"]) + float(word["height"]) for word in kept_words)
            segments.append({
                "text": " ".join(str(word["word"]) for word in kept_words),
                "left": left,
                "top": top,
                "bottom": bottom,
                "centre": (top + bottom) / 2,
                "height": bottom - top,
                "order": min(int(word["index"]) for word in kept_words),
            })

        if not segments:
            return ""

        heights = sorted(float(segment["height"]) for segment in segments)
        median_height = heights[len(heights) // 2]
        # Printed scan lines are normally further apart than one character
        # height.  This tolerance joins fragments on one baseline without
        # collapsing two adjacent wrapped lines.
        vertical_tolerance = max(6.0, median_height * 0.55)
        bands: list[dict[str, float | list[dict[str, float | int | str]]]] = []
        for segment in sorted(segments, key=lambda item: (float(item["centre"]), float(item["left"]))):
            centre = float(segment["centre"])
            if bands and abs(centre - float(bands[-1]["centre"])) <= vertical_tolerance:
                members = bands[-1]["members"]
                assert isinstance(members, list)
                members.append(segment)
                bands[-1]["centre"] = sum(float(item["centre"]) for item in members) / len(members)
            else:
                bands.append({"centre": centre, "members": [segment]})

        rendered: list[tuple[int, str]] = []
        for band in bands:
            members = band["members"]
            assert isinstance(members, list)
            members.sort(key=lambda item: (float(item["left"]), int(item["order"])))
            has_layout_fragment = any(
                len(str(member["text"]).split()) == 1
                and len(re.sub(r"[^\w]", "", str(member["text"]), flags=re.UNICODE)) <= 5
                for member in members
            )
            if has_layout_fragment and len(members) > 1:
                rendered.append((
                    min(int(member["order"]) for member in members),
                    cls._format_ocr_line(" ".join(str(member["text"]) for member in members)),
                ))
            else:
                rendered.extend(
                    (int(member["order"]), cls._format_ocr_line(str(member["text"])))
                    for member in members
                )

        rendered.sort(key=lambda item: item[0])
        lines = [line for _, line in rendered if line]
        is_contents_page = any(line.casefold() == "page" for line in lines)
        if is_contents_page:
            lines = [
                re.sub(r"^(\d{1,3}|[IVXLCDM]+)(?=\s+[A-Z\"“])", r"\1.", line, flags=re.IGNORECASE)
                for line in lines
            ]
            lines = cls._repair_contents_numbering(lines)
        return "\n".join(lines)

    @classmethod
    def _tesseract_ocr_page(cls, page: fitz.Page, page_segmentation_mode: int) -> str:
        if not _tesseract_available or pytesseract is None or Image is None:
            return ""
        try:
            # The original 300 DPI source is a useful quality/speed point for
            # printed legal scans; pages that need this are cached by
            # DocumentService after the first read.
            image = cls._render_ocr_image(page)
            if image is None:
                return ""
            if page_segmentation_mode == 11:
                data = pytesseract.image_to_data(
                    image,
                    lang="hin+eng",
                    config="--oem 3 --psm 11",
                    output_type=pytesseract.Output.DICT,
                    timeout=18,
                )
                return cls._rebuild_positioned_ocr_lines(data)
            text = pytesseract.image_to_string(
                image,
                lang="hin+eng",
                config=f"--oem 3 --psm {page_segmentation_mode}",
                timeout=18,
            )
            return cls._clean_ocr_lines(text.splitlines())
        except Exception:
            return ""

    @classmethod
    def _ocr_page(cls, page: fitz.Page) -> str:
        """Choose OCR that fits the visible scan instead of one global mode.

        RapidOCR's ONNX text recognizer is more accurate for dense printed
        prose. Contents pages are a different layout problem: their separate
        labels and right-aligned page references are handled more faithfully by
        the positioned Tesseract reconstruction already used by this reader.
        """
        rapid_text = cls._rapid_ocr_page(page)
        if rapid_text:
            rapid_lines = rapid_text.splitlines()
            if not cls._is_contents_style(rapid_lines):
                return rapid_text
            structured_text = cls._tesseract_ocr_page(page, 11)
            return structured_text or rapid_text
        # Keep a useful free fallback for deployments that do not install the
        # optional ONNX runtime. Dense mode is much more reliable for prose
        # than sparse-text mode, which was designed for unrelated fragments.
        return cls._tesseract_ocr_page(page, 4)

    def _extract_document(self, pdf: fitz.Document, start: int, end: int) -> list[dict[str, str | int]]:
        if start < 1 or end < 1 or start > pdf.page_count or end > pdf.page_count:
            raise ValueError(f"Select pages between 1 and {pdf.page_count}.")
        normalized_start, normalized_end = sorted((start, end))
        pages: list[dict[str, str | int]] = []
        for page_number in range(normalized_start, normalized_end + 1):
            page = pdf.load_page(page_number - 1)
            text = self._extract_ordered_text(page)
            if self._needs_scan_ocr(page, text):
                ocr_text = self._ocr_page(page)
                if ocr_text:
                    text = ocr_text
            text = self._clean_text(text)
            pages.append({"page": page_number, "text": text})
        return pages

    def extract(self, path: Path, start: int, end: int) -> list[dict[str, str | int]]:
        with fitz.open(path) as pdf:
            return self._extract_document(pdf, start, end)

    def extract_bytes(self, content: bytes, start: int, end: int) -> list[dict[str, str | int]]:
        with fitz.open(stream=content, filetype="pdf") as pdf:
            return self._extract_document(pdf, start, end)

    @staticmethod
    def page_count_bytes(content: bytes) -> int:
        with fitz.open(stream=content, filetype="pdf") as pdf:
            return pdf.page_count
