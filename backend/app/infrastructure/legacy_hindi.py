"""Strict XpressChanak span decoding; unknown glyphs require visual recognition.

Mapping data adapted from ozairexe/typesetu (MIT); see docs/CHANAKYA-MAPPING-LICENSE.txt.
PDF glyph names use MacRoman, while the keyboard mapping uses Windows-1252.
"""
import json
import re
from pathlib import Path

_PAIRS = json.loads(Path(__file__).with_name("chanakya_map.json").read_text(encoding="utf-8"))
# Xpress uses a spacing nukta, not the standalone half-fa of other Chanakya variants.
_PAIRS = [(a, b) for a, b in _PAIRS if a != "¸"] + [("¸", "़")]
_CONSONANT = r"[क-हक़-य़]"
_CLUSTER = _CONSONANT + r"(?:़)?(?:्" + _CONSONANT + r"़?)*"


def decode_xpress(text, font):
    if not re.fullmatch(r"(?:[A-Z]{6}\+)?XpressChanak(?:Bold|Italic)?", font):
        return None
    try:
        raw = text.replace("Δ", "∆").replace("¤", "€").encode("mac_roman")
        undefined = {0x81: "ख्", 0x8d: "फ्", 0x8f: "भ्", 0x90: "म्"}
        encoded = "".join(undefined.get(b) or bytes([b]).decode("cp1252") for b in raw)
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    # Mark positional signs before substitutions can turn their neighbours into Unicode.
    encoded = (encoded.replace("§ü", "ई").replace("§Z", "ईं").replace("ü", "\ue001").replace("ç", "\ue000")
               .replace("Z", "\ue002").replace("Ó", "'").replace("Ñ", "ः"))
    for old, new in _PAIRS:
        encoded = encoded.replace(old, new)
    # A pre-base i belongs after the whole consonant cluster, not its first half.
    encoded = re.sub("\ue000(" + _CLUSTER + ")", r"\1ि", encoded)
    encoded = re.sub("(" + _CLUSTER + r"[ािीुूृॄेैोौंँॅ]*" + ")\ue001", r"र्\1", encoded)
    encoded = re.sub("(" + _CLUSTER + r"[ािीुूृॄेैोौंँॅ]*" + ")\ue002", r"र्\1ं", encoded)
    encoded = encoded.replace("््", "्")
    if any(not ("\u0900" <= ch <= "\u097f" or ch.isspace() or ch in "0123456789.,;:!?()[]{}*-–—/'\"") for ch in encoded):
        return None
    return encoded


def decode_block(block):
    lines = []
    for line in block.get("lines", []):
        spans = []
        for span in line["spans"]:
            font, text = span.get("font", ""), span.get("text", "")
            if any(h in font.casefold() for h in ("shiva", "kruti", "chanak", "devlys")):
                text = decode_xpress(text, font)
                if text is None:
                    return None
            spans.append(text)
        lines.append("".join(spans))
    return "\n".join(lines)
