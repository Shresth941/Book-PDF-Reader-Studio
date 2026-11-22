"""Synthetic AcroForm cases: field values and stored appearances are distinct."""
from contextlib import contextmanager
import hashlib
import json

import fitz
import pytest

from app.infrastructure.form_evidence import inspect_form_evidence


@contextmanager
def form_document(field_type=fitz.PDF_WIDGET_TYPE_TEXT, value="Alice", flags=0):
    with fitz.open() as document:
        page = document.new_page()
        widget = fitz.Widget()
        widget.field_name = "applicant"
        widget.field_type = field_type
        widget.field_value = value
        widget.field_flags = flags
        widget.text_fontsize = 12
        widget.rect = fitz.Rect(30, 40, 260, 80)
        page.add_widget(widget)
        data = document.tobytes()
    with fitz.open(stream=data, filetype="pdf") as document:
        yield document


def one_field(document):
    evidence = inspect_form_evidence(document, document[0])
    assert len(evidence["widgets"]) == 1
    json.dumps(evidence, ensure_ascii=False, allow_nan=False)
    return evidence, evidence["widgets"][0]


def test_matching_text_is_evidence_and_never_automatic_approval():
    with form_document() as document:
        evidence, field = one_field(document)
        assert field["stored_value"] == "Alice"
        assert field["appearance"]["text"].strip() == "Alice"
        assert field["appearance"]["text_available"]
        assert field["status"] == "matching"
        assert field["bbox"] == [30.0, 40.0, 260.0, 80.0]
        assert field["requires_visual_reconciliation"] and not field["approved"]
        assert evidence["requires_visual_reconciliation"]
        assert any("static export" in item for item in evidence["limitations"])
        assert field["provenance"]["widget_xref"] == field["xref"]
        assert len(field["provenance"]["evidence_sha256"]) == 64


def test_stale_text_appearance_detected_without_regenerating_it():
    with form_document(value="Old Value") as document:
        before = one_field(document)[1]
        xref = before["xref"]
        ap_xref = before["appearance"]["selected_xref"]
        ap_bytes = document.xref_stream_raw(ap_xref)
        document.xref_set_key(xref, "V", fitz.get_pdf_str("New Value"))
        evidence, field = one_field(document)
        assert field["stored_pdf_value"]["value"] == "New Value"
        assert field["appearance"]["text"].strip() == "Old Value"
        assert field["status"] == "contradiction"
        assert {item["code"] for item in field["discrepancies"]} == {"stored_appearance_text_disagreement"}
        assert document.xref_stream_raw(ap_xref) == ap_bytes
        assert before["provenance"]["evidence_sha256"] != field["provenance"]["evidence_sha256"]
        assert evidence["requires_visual_reconciliation"]


@pytest.mark.parametrize("state", ["Yes", "Off"])
def test_matching_checkbox_reports_states_and_requires_visual_review(state):
    with form_document(fitz.PDF_WIDGET_TYPE_CHECKBOX, state) as document:
        _, field = one_field(document)
        assert field["status"] == "matching"
        assert set(field["button"]["normal_states"]) == {"Off", "Yes"}
        assert field["button"]["on_states"] == ["Yes"]
        assert field["button"]["export_value"] == state
        assert field["button"]["appearance_state"] == state
        assert field["appearance"]["selected_xref"]
        assert field["requires_visual_reconciliation"] and not field["approved"]


@pytest.mark.parametrize("value,appearance", [("Yes", "Off"), ("Off", "Yes")])
def test_checkbox_value_appearance_disagreement(value, appearance):
    with form_document(fitz.PDF_WIDGET_TYPE_CHECKBOX, value) as document:
        xref = one_field(document)[1]["xref"]
        document.xref_set_key(xref, "AS", "/" + appearance)
        _, field = one_field(document)
        assert field["status"] == "contradiction"
        assert field["button"]["expected_appearance_state"] == value
        assert field["button"]["appearance_state"] == appearance
        assert "button_state_disagreement" in {item["code"] for item in field["discrepancies"]}


def test_custom_checkbox_export_state_is_not_assumed_to_be_yes():
    with form_document(fitz.PDF_WIDGET_TYPE_CHECKBOX, "Yes") as document:
        xref = one_field(document)[1]["xref"]
        yes = document.xref_get_key(xref, "AP/N/Yes")[1]
        off = document.xref_get_key(xref, "AP/N/Off")[1]
        document.xref_set_key(xref, "AP/N", f"<< /Off {off} /Accepted {yes} >>")
        document.xref_set_key(xref, "V", "/Accepted")
        document.xref_set_key(xref, "AS", "/Accepted")
        _, field = one_field(document)
        assert field["status"] == "matching"
        assert field["button"]["on_states"] == ["Accepted"]
        assert field["button"]["export_value"] == "Accepted"


def test_inherited_value_provenance_and_radio_off_state():
    with form_document(fitz.PDF_WIDGET_TYPE_CHECKBOX, "Off") as document:
        xref = one_field(document)[1]["xref"]
        parent = document.get_new_xref()
        document.update_object(parent, f"<< /FT /Btn /Ff {fitz.PDF_BTN_FIELD_IS_RADIO} /T (group) /V /Other /Kids [{xref} 0 R] >>")
        document.xref_set_key(xref, "Parent", f"{parent} 0 R")
        document.xref_set_key(xref, "Ff", str(fitz.PDF_BTN_FIELD_IS_RADIO))
        document.xref_set_key(xref, "V", "null")
        _, field = one_field(document)
        assert field["stored_pdf_value"]["value"] == "/Other"
        assert field["provenance"]["value_xref"] == parent
        assert parent in field["provenance"]["parent_chain"]
        assert field["button"]["expected_appearance_state"] == "Off"
        assert field["status"] == "matching"
        assert any("other group widgets" in message for message in field["warnings"])


def test_missing_appearance_remains_unknown_and_is_not_generated():
    with form_document() as document:
        xref = one_field(document)[1]["xref"]
        document.xref_set_key(xref, "AP", "null")
        _, field = one_field(document)
        assert not field["appearance"]["present"]
        assert field["status"] == "unknown"
        assert not field["appearance"]["text_available"]
        assert document.xref_get_key(xref, "AP")[0] == "null"


def test_formatting_action_does_not_become_false_discrepancy_or_execute():
    with form_document(value="1,000") as document:
        xref = one_field(document)[1]["xref"]
        document.xref_set_key(xref, "V", fitz.get_pdf_str("1000"))
        script = fitz.get_pdf_str('this.getField("applicant").value = "EXECUTED";')
        document.xref_set_key(xref, "AA", f"<< /F << /S /JavaScript /JS {script} >> >>")
        _, field = one_field(document)
        assert field["status"] == "unknown"
        assert field["appearance"]["text"].strip() == "1,000"
        assert not field["discrepancies"]
        assert document.xref_get_key(xref, "V")[1] == "1000"
        assert any("actions were not executed" in item for item in field["warnings"])


def test_signature_read_only_flags_and_static_limitations():
    with form_document(flags=fitz.PDF_FIELD_IS_READ_ONLY | fitz.PDF_FIELD_IS_REQUIRED | fitz.PDF_FIELD_IS_NO_EXPORT) as document:
        xref = one_field(document)[1]["xref"]
        document.xref_set_key(xref, "FT", "/Sig")
        document.xref_set_key(xref, "V", "null")
        _, field = one_field(document)
        assert field["signature"]["is_signature"]
        assert field["signature"]["is_signed"] is False
        assert field["signature"]["validity_verified"] is False
        assert field["read_only"] and field["required"] and field["no_export"]
        assert field["status"] == "unknown"
        assert any("does not inherit" in item for item in field["warnings"])


def test_xfa_is_reported_unsupported_without_parsing_or_execution():
    with form_document() as document:
        packet = document.get_new_xref()
        document.update_object(packet, "<< >>")
        document.update_stream(packet, b"<xfa>UNSUPPORTED DYNAMIC CONTENT</xfa>")
        document.xref_set_key(document.pdf_catalog(), "AcroForm/XFA", f"{packet} 0 R")
        result, _ = one_field(document)
        assert result["xfa"]["present"]
        assert result["xfa"]["status"] == "unsupported"
        assert result["requires_visual_reconciliation"]
        assert document.xref_stream(packet) == b"<xfa>UNSUPPORTED DYNAMIC CONTENT</xfa>"


def test_source_file_and_objects_are_unchanged_by_inspection(tmp_path):
    source = tmp_path / "synthetic-form.pdf"
    with form_document() as document:
        document.save(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    with fitz.open(source) as document:
        objects = [document.xref_object(xref) for xref in range(1, document.xref_length())]
        result, _ = one_field(document)
        assert result == inspect_form_evidence(document, document[0])
        assert objects == [document.xref_object(xref) for xref in range(1, document.xref_length())]
        assert not document.is_dirty
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_nonform_page_has_no_reconciliation_requirement():
    with fitz.open() as document:
        page = document.new_page()
        result = inspect_form_evidence(document, page)
        assert not result["widgets"]
        assert not result["requires_visual_reconciliation"]


def test_signed_flag_does_not_certify_invalid_signature_bytes():
    with form_document() as document:
        xref = one_field(document)[1]["xref"]
        document.xref_set_key(xref, "FT", "/Sig")
        document.xref_set_key(xref, "V", "<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached /ByteRange [0 1 2 3] /Contents <010203> >>")
        _, field = one_field(document)
        assert field["signature"]["is_signature"]
        assert field["signature"]["is_signed"] is True
        assert field["signature"]["validity_verified"] is False
        assert field["status"] == "unknown" and not field["approved"]


def test_button_state_without_corresponding_appearance_is_flagged():
    with form_document(fitz.PDF_WIDGET_TYPE_CHECKBOX, "Yes") as document:
        xref = one_field(document)[1]["xref"]
        document.xref_set_key(xref, "AP/N/Yes", "null")
        _, field = one_field(document)
        assert field["status"] != "matching"
        assert field["appearance"]["selected_xref"] is None
        assert field["requires_visual_reconciliation"]


def test_empty_appearance_text_does_not_prove_a_visually_empty_field():
    with form_document(value="") as document:
        _, field = one_field(document)
        assert field["status"] == "unknown"
        assert field["requires_visual_reconciliation"]
