"""Explicit form evidence mapping. Never infer identity from corrected text."""
from copy import deepcopy
import math

from app.core.review import ReviewError


def widget_xref(region):
    evidence = region.get("form_evidence") or {}
    xref = evidence.get("xref", evidence.get("provenance", {}).get("widget_xref"))
    if xref is None:
        return None
    if type(xref) is not int or xref <= 0 or evidence.get("provenance", {}).get("widget_xref", xref) != xref:
        raise ReviewError("form_identity_conflict", "A saved form identity is inconsistent. Its existing evidence and edits were retained.")
    return xref


def same_box(left, right):
    return (isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)) and len(left) == len(right) == 4
            and all(isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isfinite(a) and math.isfinite(b)
                    and abs(a - b) <= 0.01 for a, b in zip(left, right)))


def validate_inspection(page, inspection, document_sha256):
    if inspection.get("document_sha256") != document_sha256 or inspection.get("number") != page["number"]:
        raise ReviewError("form_snapshot_mismatch", "The form result does not belong to this original and page.", 409)
    if not same_box([0, 0, page["width"], page["height"]], [0, 0, inspection.get("width"), inspection.get("height")]):
        raise ReviewError("form_geometry_mismatch", "The form coordinate system differs from the saved page. No evidence was replaced.", 409)
    forms = inspection.get("form_evidence", {})
    if forms.get("inventory_complete") is not True or forms.get("xfa", {}).get("present"):
        raise ReviewError("form_inventory_incomplete", "A complete static form inventory is required before applying evidence.")
    widgets, proposed = forms.get("widgets", []), inspection.get("regions", [])
    xrefs = [field.get("xref") for field in widgets]
    if (any(type(x) is not int or x <= 0 for x in xrefs) or len(set(xrefs)) != len(xrefs)
            or len(proposed) != len(widgets) or [widget_xref(r) for r in proposed] != xrefs
            or any(r.get("method") != "form-field" or r.get("form_evidence") != field for r, field in zip(proposed, widgets))):
        raise ReviewError("form_inventory_invalid", "The inspected form inventory has missing or duplicate identities.")


def mapping_suggestions(page, inspection):
    old = [r for r in page["regions"] if r.get("method") == "form-field"]
    candidates = {}
    for proposal in inspection["regions"]:
        xref = widget_xref(proposal)
        possible = [r["id"] for r in old if same_box(r["bbox"], proposal["bbox"])
                    and widget_xref(r) in (None, xref)]
        candidates[str(xref)] = possible
    counts = {region_id: sum(region_id in ids for ids in candidates.values()) for r in old for region_id in [r["id"]]}
    # Suggestions are never committed implicitly. Overlaps require an explicit choice.
    return {key: ids[0] if len(ids) == 1 and counts[ids[0]] == 1 else None for key, ids in candidates.items()}


def merge_form_evidence(page, inspection, bindings):
    """Return a separate page and audit detail; caller performs revision checks."""
    result = deepcopy(page)
    old = {r["id"]: r for r in result["regions"] if r.get("method") == "form-field"}
    proposals = {widget_xref(r): r for r in inspection["regions"]}
    xrefs = [b["widget_xref"] for b in bindings]
    ids = [b["region_id"] for b in bindings if b["region_id"] is not None]
    if len(xrefs) != len(set(xrefs)) or set(xrefs) != set(proposals) or len(ids) != len(set(ids)) or set(ids) != set(old):
        raise ReviewError("form_mapping_incomplete", "Map each original field once and every existing form region once. Add separate regions only for newly identified fields.")
    changes = []
    for binding in bindings:
        proposal = proposals[binding["widget_xref"]]
        region_id = binding["region_id"]
        if region_id is None:
            added = deepcopy(proposal)
            added["order"] = len(result["regions"])
            result["regions"].append(added)
            changes.append({"region": added["id"], "before": None, "after": deepcopy(added)})
            continue
        current = old[region_id]
        previous_xref = widget_xref(current)
        if previous_xref not in (None, binding["widget_xref"]) or not same_box(current["bbox"], proposal["bbox"]):
            raise ReviewError("form_mapping_identity", "The selected field differs from this saved region's identity or position. No mappings were applied.")
        if current.get("form_evidence") != proposal["form_evidence"]:
            before = deepcopy(current)
            current["form_evidence"] = deepcopy(proposal["form_evidence"])
            current["form_evidence_changed"] = True
            current["source_approved"] = current["translation_approved"] = False
            current["acknowledged"] = {}
            changes.append({"region": region_id, "before": before, "after": deepcopy(current)})
    inventory_changed = result.get("form_evidence") != inspection["form_evidence"]
    result["form_evidence"] = deepcopy(inspection["form_evidence"])
    return result, changes, bool(changes or inventory_changed)


def form_inventory_blockers(page):
    forms = page.get("form_evidence")
    if not forms:
        return []
    if forms.get("inventory_complete") is False or any("inventory" in warning.lower() and ("incomplete" in warning.lower() or "not be read" in warning.lower()) for warning in forms.get("limitations", [])):
        return ["The form field inventory is incomplete. Refresh its evidence or obtain a static printable source before completing this page."]
    blockers = []
    regions = [r for r in page["regions"] if r.get("method") == "form-field"]
    for field in forms.get("widgets", []):
        matches = [r for r in regions if (r.get("form_evidence") or {}).get("xref") == field.get("xref")]
        if len(matches) != 1 or matches[0].get("form_evidence") != field:
            blockers.append(f"Form field {field.get('field_name') or field.get('xref')} needs a unique, current evidence mapping. Inspect and apply the form evidence mappings.")
    return blockers
