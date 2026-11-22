"""Read-only AcroForm evidence for the pinned PyMuPDF 1.25.1 worker.

``matching`` compares stored data with available appearance evidence, never visual
correctness or signature validity. All widgets require human reconciliation. No
widget updates, PDF saves, action execution, or XFA processing take place here.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata

import fitz


MAX_PARENT_DEPTH = 32
MAX_WIDGETS = 2000
MAX_APPEARANCE_BYTES = 2 * 1024 * 1024


def _ref(value):
    match = re.fullmatch(r"(\d+)\s+\d+\s+R", value)
    return int(match[1]) if match else None


def _name(value):
    value = value.removeprefix("/")
    # xref_get_key normally decodes PDF names, whereas button_states may retain
    # #XX escapes. Normalize the two representations before comparing states.
    return re.sub(r"#([0-9A-Fa-f]{2})", lambda m: chr(int(m[1], 16)), value)


def _normalized_text(value):
    return " ".join(unicodedata.normalize("NFC", value).split())


def _inherited_value(document, xref):
    current, seen, parents, value = xref, set(), [], None
    warnings = []
    for _ in range(MAX_PARENT_DEPTH):
        if current in seen:
            warnings.append("Field parent cycle prevents complete inheritance inspection.")
            break
        seen.add(current)
        kind, raw = document.xref_get_key(current, "V")
        if value is None and kind != "null":
            value = {"type": kind, "value": raw, "xref": current}
        parent_kind, parent_raw = document.xref_get_key(current, "Parent")
        if parent_kind == "null":
            break
        parent = _ref(parent_raw) if parent_kind == "xref" else None
        if parent is None:
            warnings.append("The field parent is not an inspectable indirect object.")
            break
        parents.append(parent)
        current = parent
    else:
        warnings.append("Field parent depth exceeds the inspection limit.")
    return value or {"type": "null", "value": "null", "xref": None}, parents, warnings


def _selected_appearance(document, widget, states, appearance_state):
    normal_type, normal_value = document.xref_get_key(widget.xref, "AP/N")
    normal_xref = _ref(normal_value) if normal_type == "xref" else None
    selected_xref = None
    warnings = []
    if normal_xref and document.xref_is_stream(normal_xref):
        selected_xref = normal_xref
    elif normal_type != "null" and appearance_state is not None:
        raw_state = next((s for s in states if _name(s) == appearance_state), appearance_state)
        try:
            kind, value = document.xref_get_key(widget.xref, "AP/N/" + raw_state)
            selected_xref = _ref(value) if kind == "xref" else None
            if selected_xref and not document.xref_is_stream(selected_xref):
                selected_xref = None
        except (ValueError, RuntimeError):
            warnings.append("The selected appearance state could not be resolved.")
    appearance = {"present": normal_type != "null", "normal_type": normal_type,
                  "normal_xref": normal_xref, "selected_xref": selected_xref,
                  "selected_stream_sha256": None, "text": None, "text_available": False}
    if selected_xref:
        kind, length = document.xref_get_key(selected_xref, "Length")
        if kind == "int" and int(length) > MAX_APPEARANCE_BYTES:
            warnings.append("Appearance stream exceeds the evidence inspection limit.")
            return appearance, warnings
        raw = document.xref_stream_raw(selected_xref)
        if raw is not None and len(raw) <= MAX_APPEARANCE_BYTES:
            appearance["selected_stream_sha256"] = hashlib.sha256(raw).hexdigest()
        else:
            warnings.append("Appearance stream is missing or exceeds the evidence inspection limit.")
    return appearance, warnings


def _widget_evidence(document, page, widget):
    xref = widget.xref
    stored, parents, warnings = _inherited_value(document, xref)
    flags = int(widget.field_flags or 0)
    field_type = widget.field_type
    is_button = field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON)
    raw_states = widget.button_states() if is_button else None
    raw_states = raw_states or {}
    normal_states = [_name(s) for s in raw_states.get("normal") or []]
    down_states = [_name(s) for s in raw_states.get("down") or []]
    state_kind, state_value = document.xref_get_key(xref, "AS")
    appearance_state = _name(state_value) if state_kind == "name" else None
    export_value = _name(stored["value"]) if stored["type"] == "name" else None
    appearance, appearance_warnings = _selected_appearance(document, widget, raw_states.get("normal") or [], appearance_state)
    warnings.extend(appearance_warnings)
    is_signature = field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE
    result = {
        "xref": xref, "page_number": page.number + 1,
        "field_name": widget.field_name or "", "field_type": widget.field_type_string or "Unknown",
        "field_type_id": field_type, "stored_value": widget.field_value,
        "stored_pdf_value": stored, "bbox": list(widget.rect), "flags": flags,
        "read_only": bool(flags & fitz.PDF_FIELD_IS_READ_ONLY),
        "required": bool(flags & fitz.PDF_FIELD_IS_REQUIRED),
        "no_export": bool(flags & fitz.PDF_FIELD_IS_NO_EXPORT),
        "signature": {"is_signature": is_signature, "is_signed": bool(widget.is_signed) if is_signature else None,
                      "validity_verified": False},
        "button": {"normal_states": normal_states, "down_states": down_states,
                   "on_states": [s for s in normal_states if s != "Off"], "off_state": "Off" if "Off" in normal_states else None,
                   "export_value": export_value, "appearance_state": appearance_state, "expected_appearance_state": None},
        "appearance": appearance, "status": "unknown", "discrepancies": [], "warnings": warnings,
        "requires_visual_reconciliation": True, "approved": False,
        "provenance": {"widget_xref": xref, "value_xref": stored["xref"], "parent_chain": parents},
    }

    def discrepancy(code, message):
        result["discrepancies"].append({"code": code, "message": message})
        result["status"] = "contradiction"

    if not appearance["present"] or not appearance["selected_xref"]:
        warnings.append("No usable selected normal appearance; compare the original in a qualified viewer.")
    if is_signature:
        warnings.append("Signature appearance and signed flag do not establish cryptographic validity. A static translated derivative does not inherit the original signature.")
    elif is_button:
        expected = None
        if export_value == "Off" or stored["type"] == "null":
            expected = "Off"
        elif export_value in result["button"]["on_states"]:
            expected = export_value
        elif field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON and export_value is not None:
            # Another widget in the shared group may own the selected value.
            expected = "Off"
            warnings.append("This radio is not the selected group value; reconcile the other group widgets as well.")
        elif export_value is not None:
            discrepancy("unknown_button_value", "The stored button value has no corresponding normal appearance state.")
        result["button"]["expected_appearance_state"] = expected
        if expected is not None and appearance_state is not None:
            if expected != appearance_state:
                discrepancy("button_state_disagreement", "Stored button value and selected appearance state disagree.")
            elif appearance["selected_xref"] and result["status"] != "contradiction":
                result["status"] = "matching"
        if appearance_state is not None and appearance_state not in normal_states:
            discrepancy("missing_button_appearance", "The selected button appearance state has no available normal appearance.")
    elif field_type == fitz.PDF_WIDGET_TYPE_TEXT and appearance["selected_stream_sha256"]:
        # The annotation TextPage reads AP, unlike page body extraction. This is
        # a private bridge in the pinned PyMuPDF version, so failure is unknown
        # evidence rather than a guessed stored-value rendering.
        try:
            text = widget._annot.get_textpage().extractText()
            appearance.update(text=text, text_available=True)
        except (AttributeError, ValueError, RuntimeError):
            warnings.append("The existing appearance text could not be extracted.")
        special_flags = fitz.PDF_TX_FIELD_IS_PASSWORD | fitz.PDF_TX_FIELD_IS_RICH_TEXT
        actions = any(document.xref_get_key(node, key)[0] != "null"
                      for node in [xref, *parents] for key in ("AA/F", "AA/C"))
        if actions or flags & special_flags:
            warnings.append("Formatting, calculation, password or rich-text behavior requires visual reconciliation; actions were not executed.")
        elif appearance["text_available"] and stored["type"] in ("string", "null"):
            visible = appearance["text"]
            value = stored["value"] if stored["type"] == "string" else ""
            if "\ufffd" in visible or any(ord(c) < 32 and c not in "\t\n\r" for c in visible):
                warnings.append("Appearance text has uncertain character decoding.")
            elif not visible.strip():
                warnings.append("The appearance contains no extractable text; it may use outlines or images.")
            elif _normalized_text(value) == _normalized_text(visible):
                result["status"] = "matching"
            else:
                discrepancy("stored_appearance_text_disagreement", "Stored field text differs from text extracted from its existing appearance.")
    else:
        warnings.append("This field type requires visual reconciliation; no deterministic text comparison is available.")
    if warnings and any("parent" in warning.lower() for warning in warnings) and result["status"] == "matching":
        result["status"] = "unknown"
    result["provenance"]["evidence_sha256"] = hashlib.sha256(
        json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()
    return result


def inspect_form_evidence(document, page):
    """Return JSON-safe evidence for one page; never approve or modify fields.

    ``bbox`` uses PyMuPDF's unrotated page coordinates. Bind the returned widget
    xref and evidence hash to the document fingerprint and review revision in the
    caller. Run this helper inside the existing resource-limited PDF worker.
    """
    if not document.is_pdf or page.parent is not document:
        raise ValueError("Form evidence requires a page from the supplied PDF document.")
    xfa_type, _ = document.xref_get_key(document.pdf_catalog(), "AcroForm/XFA")
    xfa_present = xfa_type != "null"
    result = {"page_number": page.number + 1, "xfa": {
        "present": xfa_present, "status": "unsupported" if xfa_present else "absent",
        "message": "XFA is present. Dynamic rendering/data binding is unsupported and was not executed." if xfa_present else ""},
        "widgets": [], "inventory_complete": True, "requires_visual_reconciliation": xfa_present, "limitations": []}
    try:
        for index, widget in enumerate(page.widgets() or []):
            if index >= MAX_WIDGETS:
                result["inventory_complete"] = False
                result["limitations"].append("Widget count exceeds the per-page inspection limit; the inventory is incomplete.")
                break
            try:
                result["widgets"].append(_widget_evidence(document, page, widget))
            except (ValueError, RuntimeError, AttributeError, TypeError, OverflowError):
                result["widgets"].append({"xref": widget.xref, "page_number": page.number + 1,
                    "field_name": widget.field_name or "", "bbox": list(widget.rect), "status": "unknown",
                    "field_type": widget.field_type_string or "Unknown", "field_type_id": widget.field_type,
                    "stored_value": widget.field_value, "stored_pdf_value": {"type": "unknown", "value": None, "xref": None},
                    "flags": int(widget.field_flags or 0), "read_only": bool(widget.field_flags & fitz.PDF_FIELD_IS_READ_ONLY),
                    "required": bool(widget.field_flags & fitz.PDF_FIELD_IS_REQUIRED), "no_export": bool(widget.field_flags & fitz.PDF_FIELD_IS_NO_EXPORT),
                    "signature": {"is_signature": widget.field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE,
                                  "is_signed": None, "validity_verified": False},
                    "button": {"normal_states": [], "down_states": [], "on_states": [], "off_state": None,
                               "export_value": None, "appearance_state": None, "expected_appearance_state": None},
                    "appearance": {"present": None, "normal_type": "unknown", "normal_xref": None, "selected_xref": None,
                                   "selected_stream_sha256": None, "text": None, "text_available": False},
                    "discrepancies": [], "warnings": ["The widget could not be inspected completely."],
                    "requires_visual_reconciliation": True, "approved": False,
                    "provenance": {"widget_xref": widget.xref, "value_xref": None, "parent_chain": [], "evidence_sha256": None}})
    except (ValueError, RuntimeError):
        result["inventory_complete"] = False
        result["limitations"].append("The widget inventory could not be read completely.")
    if result["widgets"] or result["limitations"]:
        result["requires_visual_reconciliation"] = True
        result["limitations"].append("All form fields need visual reconciliation before static export; matching data does not prove appearance correctness.")
    if xfa_present:
        result["limitations"].append(result["xfa"]["message"])
    return result
