import fitz
import pytest
from app.core.review import ReviewError
from app.services.review_export import build_export
from test_review_workflow import workspace, source_pdf, approve_all


@pytest.mark.parametrize("runtime", ["LOCAL", "development", "", "hostde"])
def test_invalid_runtime_cannot_enable_network_paths(runtime):
    from pydantic import ValidationError
    from app.core.config import Settings
    with pytest.raises(ValidationError):
        Settings(_env_file=None, runtime_mode=runtime)


def test_export_checks_each_repeated_occurrence_and_output_page(tmp_path):
    from app.services.review_export import verify_pdf_content
    pdf = fitz.open()
    for _ in range(2):
        page = pdf.new_page()
        page.insert_text((50, 60), "Repeated legal clause.")
    mapping = [{"source_page": n, "region_id": str(n), "expected": "Repeated legal clause.",
                "spans": [{"page": n, "bbox": [40, 40, 400, 80]}]} for n in (1, 2)]
    verify_pdf_content(pdf, mapping)
    pdf[1].add_redact_annot(fitz.Rect(40, 40, 400, 80))
    pdf[1].apply_redactions()
    with pytest.raises(ReviewError, match="verification failed for page 2"):
        verify_pdf_content(pdf, mapping)
    pdf.close()


def test_export_checks_repeated_occurrences_in_distinct_boxes():
    from app.services.review_export import verify_pdf_content
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((50, 60), "Repeated legal clause.")
    page.insert_text((50, 160), "Repeated legal clause.")
    mapping = [{"source_page": 1, "region_id": str(n), "expected": "Repeated legal clause.",
                "spans": [{"page": 1, "bbox": [40, y, 400, y + 40]}]} for n, y in ((1, 40), (2, 140))]
    verify_pdf_content(pdf, mapping)
    page.add_redact_annot(fitz.Rect(40, 140, 400, 180))
    page.apply_redactions()
    with pytest.raises(ReviewError, match="region 2"):
        verify_pdf_content(pdf, mapping)
    pdf.close()


@pytest.mark.parametrize(("expected", "actual"), [
    ("not able", "notable"),
    ("Total: 100", "Total: 1000"),
    ("The debtor owes 100.", "The debtor owes 100. The debtor owes 100."),
    ("The debtor shall pay.", "The debtor shall not pay."),
])
def test_export_rejects_changed_spacing_extra_facts_and_duplicate_content(expected, actual):
    from app.services.review_export import verify_pdf_content
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 60), actual)
        mapping = [{"source_page": 1, "region_id": "changed", "expected": expected,
                    "spans": [{"page": 1, "bbox": [40, 40, 550, 80]}]}]
        with pytest.raises(ReviewError, match="verification failed for page 1"):
            verify_pdf_content(pdf, mapping)


def test_export_allows_whitespace_from_normal_line_wrapping():
    from app.services.review_export import verify_pdf_content
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 60), "The debtor shall\npay 100 rupees.")
        mapping = [{"source_page": 1, "region_id": "wrapped", "expected": "The debtor shall pay 100 rupees.",
                    "spans": [{"page": 1, "bbox": [40, 40, 550, 100]}]}]
        verify_pdf_content(pdf, mapping)


def test_long_hindi_does_not_silently_shrink_below_floor(workspace, tmp_path):
    store, identifier = workspace
    approve_all(store, identifier)
    snapshot = store.snapshot(identifier, store.document(identifier)["revision"])
    snapshot["pages"][0]["regions"][0]["translation"] = "यह बहुत लंबा वाक्य है। " * 100
    with pytest.raises(ReviewError, match="does not fit"):
        build_export(snapshot, str(store.root / "originals" / f"{identifier}.pdf"), str(tmp_path / "overflow.pdf"), "pdf", "faithful")


def test_companion_and_docx_preserve_reviewed_content(workspace, tmp_path):
    from docx import Document
    store, identifier = workspace
    approve_all(store, identifier)
    snapshot = store.snapshot(identifier, store.document(identifier)["revision"])
    original = str(store.root / "originals" / f"{identifier}.pdf")
    pdf = tmp_path / "companion.pdf"
    result = build_export(snapshot, original, str(pdf), "pdf", "companion")
    assert result["page_count"] >= 2
    with fitz.open(pdf) as exported:
        assert len(exported[0].get_images()) > 0
        assert "ऋणी" in exported[-1].get_text()
    docx = tmp_path / "translation.docx"
    build_export(snapshot, original, str(docx), "docx", "readable")
    assert "ऋणी" in "\n".join(p.text for p in Document(docx).paragraphs)


def test_readable_text_map_follows_automatic_page_breaks(workspace, tmp_path):
    store, identifier = workspace
    approve_all(store, identifier)
    snapshot = store.snapshot(identifier, store.document(identifier)["revision"])
    snapshot["pages"][0]["regions"][0]["translation"] = "यह लंबे खंड का सत्यापित वाक्य है। " * 200
    result = build_export(snapshot, str(store.root / "originals" / f"{identifier}.pdf"),
                          str(tmp_path / "long-readable.pdf"), "pdf", "readable")
    assert result["page_count"] > 1
    assert len(result["content_map"][0]["spans"]) > 1
    assert result["text_verified"]
    assert result["compatibility_issues"] == []
    assert result["text_encoding"] == "verified-vector-and-logical-text-v1"
    with fitz.open(tmp_path / "long-readable.pdf") as pdf:
        assert "".join(p.get_text() for p in pdf).count("सत्यापित") == 200


def test_actual_text_cannot_conceal_a_missing_visual_glyph(workspace, tmp_path):
    store, identifier = workspace
    approve_all(store, identifier)
    snapshot = store.snapshot(identifier, store.document(identifier)["revision"])
    snapshot["pages"][0]["regions"][0]["translation"] = "Unsupported character: \U0010FFFF"
    with pytest.raises(ReviewError, match="font cannot display"):
        build_export(snapshot, str(store.root / "originals" / f"{identifier}.pdf"),
                     str(tmp_path / "missing-glyph.pdf"), "pdf", "readable")


def test_faithful_hindi_layer_remains_transparent_over_source_graphics(workspace, tmp_path):
    store, identifier = workspace
    approve_all(store, identifier)
    snapshot = store.snapshot(identifier, store.document(identifier)["revision"])
    regions = snapshot["pages"][0]["regions"]
    regions[0]["bbox"], regions[1]["bbox"] = [68, 54, 520, 92], [68, 96, 520, 134]
    regions[0]["translation"] += " सत्यापित"
    source = tmp_path / "colored-source.pdf"
    with fitz.open(store.root / "originals" / f"{identifier}.pdf") as pdf:
        pdf[0].draw_rect(fitz.Rect(60, 40, 550, 170), color=None, fill=(0.8, 0.9, 1), overlay=False)
        pdf.save(source)
    output = tmp_path / "faithful-hindi.pdf"
    build_export(snapshot, str(source), str(output), "pdf", "faithful")
    with fitz.open(source) as original, fitz.open(output) as translated:
        assert "सत्यापित" in translated[0].get_text()
        assert "The debtor" not in translated[0].get_text()
        assert original[0].get_pixmap().pixel(70, 150) == translated[0].get_pixmap().pixel(70, 150)


def test_retained_hindi_cannot_bypass_authored_text_qualification(tmp_path):
    from app.infrastructure.structured_pdf import region
    from app.services.review_export import shaped_document
    text = "सत्यापित सत्यापित सत्यापित"
    pdf = shaped_document(tmp_path)
    pdf.add_page()
    pdf.multi_cell(0, 18, text=text)
    original = tmp_path / "unqualified-source.pdf"
    # Deliberately retain the pre-conversion original with secondary glyphs.
    original.write_bytes(bytes(pdf.output()))
    original_bytes = original.read_bytes()
    item = region("paragraph", [40, 40, 550, 90], text, disposition="already_target", source_language="hi")
    snapshot = {"document": {"filename": "source.pdf", "revision": 1}, "blockers": [],
                "pages": [{"number": 1, "regions": [item]}]}
    with pytest.raises(ReviewError) as failure:
        build_export(snapshot, str(original), str(tmp_path / "blocked.pdf"), "pdf", "faithful")
    assert failure.value.code == "retained_text_encoding_unqualified"
    assert original.read_bytes() == original_bytes
    readable = tmp_path / "readable.pdf"
    result = build_export(snapshot, str(original), str(readable), "pdf", "readable")
    from app.services.review_export import PDF_VERIFICATION_VERSION
    assert result["verification_version"] == PDF_VERIFICATION_VERSION
    assert result["text_encoding_scope"].startswith("app-authored text")
    with fitz.open(readable) as generated:
        assert generated[0].get_text().count("सत्यापित") == 3


def test_fully_replaced_hindi_can_export_in_english(tmp_path):
    from app.infrastructure.structured_pdf import region
    from app.services.review_export import shaped_document
    pdf = shaped_document(tmp_path)
    pdf.add_page()
    pdf.multi_cell(0, 18, text="सत्यापित वाक्य")
    original = tmp_path / "hindi-source.pdf"
    original.write_bytes(bytes(pdf.output()))
    item = region("paragraph", [40, 40, 550, 90], "सत्यापित वाक्य", translation="Verified sentence.", source_language="hi")
    snapshot = {"document": {"filename": "source.pdf", "revision": 1}, "blockers": [],
                "pages": [{"number": 1, "regions": [item]}]}
    output = tmp_path / "english.pdf"
    build_export(snapshot, str(original), str(output), "pdf", "faithful")
    with fitz.open(output) as generated:
        assert generated[0].get_text().strip() == "Verified sentence."


@pytest.mark.parametrize("translated_index", [0, 1])
def test_mixed_faithful_output_must_follow_approved_reading_order(workspace, tmp_path, translated_index):
    store, identifier = workspace
    approve_all(store, identifier)
    snapshot = store.snapshot(identifier, store.document(identifier)["revision"])
    regions = snapshot["pages"][0]["regions"]
    for i, item in enumerate(regions):
        item["bbox"] = [65, 50 + 40 * i, 540, 90 + 40 * i]
        item["disposition"] = "translate" if i == translated_index else "already_target"
        item["translation"] = "Approved translated paragraph."
    output = tmp_path / "mixed.pdf"
    args = (snapshot, str(store.root / "originals" / f"{identifier}.pdf"), str(output), "pdf", "faithful")
    if translated_index == 0:
        # Retained second paragraph precedes the first-paragraph overlay in
        # stream order, although both allocations display correctly.
        with pytest.raises(ReviewError) as failure:
            build_export(*args)
        assert failure.value.code == "layout_reading_order"
    else:
        build_export(*args)
        with fitz.open(output) as generated:
            assert generated[0].get_text().strip().splitlines() == [regions[0]["source"], regions[1]["translation"]]


def test_page_limit_is_enforced(source_pdf):
    from app.infrastructure.structured_pdf import inspect_pdf
    with pytest.raises(ReviewError, match="1 to 0"):
        inspect_pdf(str(source_pdf), max_pages=0)
