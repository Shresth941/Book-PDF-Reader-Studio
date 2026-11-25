"""Pure review rules. Scores are evidence; only explicit review can approve content."""
from __future__ import annotations

from collections import Counter
import re
import unicodedata


class ReviewError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def language_hint(text: str) -> str:
    """Scripts cannot distinguish English/French or Hindi/Marathi reliably."""
    latin = bool(re.search(r"[A-Za-z]", text))
    deva = bool(re.search(r"[\u0900-\u097f]", text))
    return "mixed" if latin and deva else "devanagari" if deva else "latin" if latin else "uncertain"


def numeric_tokens(text: str) -> Counter:
    normalized = "".join(str(unicodedata.digit(c)) if c.isdigit() and c.isdecimal() else c for c in text)
    return Counter(re.findall(r"\d+(?:[.,:/-]\d+)*%?", normalized))


def translation_issues(source: str, target: str, glossary: list[dict] | None = None) -> list[dict]:
    issues = []
    def add(code, message):
        issues.append({"code": code, "message": message, "blocking": True})
    if source.strip() and not target.strip():
        add("missing_translation", "This source passage has no translation.")
    if target.strip() and numeric_tokens(source) != numeric_tokens(target):
        add("numbers_changed", "Numbers, dates, units or references differ. Check them against the original.")
    if target.strip() and source.strip() == target.strip():
        add("unchanged_translation", "The result equals the source. Confirm language and disposition.")
    if re.search(r"(?i)\b(not|unless|shall|may|except|notwithstanding)\b|नहीं|चाहिए|किन्तु|परंतु", source):
        add("legal_scope", "Review obligations, permissions, negation and exceptions in their full clause context.")
    if numeric_tokens(source) or re.search(r"(?i)\b(owes|pay|paid|debtor|creditor|liable)\b|देय|ऋणी|लेनदार", source):
        add("fact_relationships", "Verify who owes/receives each amount, which action/date it belongs to, and its unit. Matching numbers alone is insufficient.")
    for entry in glossary or []:
        compact = lambda text: re.sub(r"\s+", " ", text).strip().casefold()
        if entry.get("source") and compact(entry["source"]) in compact(source) and compact(entry.get("target", "")) not in compact(target):
            add("glossary:" + entry["id"], f"Check approved term: {entry['source']} → {entry['target']} ({entry.get('sense', '')}).")
    return issues


def region_blockers(region: dict, glossary: list[dict], target: str | None = None) -> list[str]:
    if region.get("method") == "form-field" and not region.get("form_evidence", {}).get("provenance", {}).get("evidence_sha256"):
        return ["Form evidence could not be inspected completely. This field must remain unresolved until a static printable source can be verified."]
    if region.get("method") == "form-field" and len(region.get("form_review_note", "").strip()) < 10:
        return ["Compare the stored form value with its visible appearance and record how any disagreement was resolved."]
    disposition = region.get("disposition", "unreadable")
    if disposition in ("unreadable", "excluded"):
        return ["Unresolved or excluded content keeps the document partial."]
    if region.get("contains_text") and disposition == "preserve":
        return ["An image with untranslated text is not a completed translation."]
    if disposition == "decorative" and not region.get("reason", "").strip():
        return ["Explain why this region is decorative or blank."]
    if not region.get("source_approved"):
        return ["Source recognition has not been reviewed."]
    if disposition in ("translate", "already_target") and not region.get("source", "").strip():
        return ["Text regions must contain a verified transcription."]
    if disposition == "already_target" and (target not in ("en", "hi") or region.get("source_language") != target):
        return ["Confirm that this region's source language matches the document target, or change its disposition to translate."]
    if disposition == "translate":
        if region.get("source_language") not in ("en", "hi"):
            return ["Confirm Hindi or English as the source language."]
        if not region.get("translation", "").strip():
            return ["Translation is missing."]
        if not region.get("translation_approved"):
            return ["Translation has not been reviewed."]
        acknowledged = region.get("acknowledged", {})
        return [i["message"] for i in translation_issues(region["source"], region["translation"], glossary)
                if not acknowledged.get(i["code"], "").strip()]
    return []


def page_blockers(page: dict, glossary: list[dict], target: str | None = None) -> list[str]:
    from app.core.structure_review import table_blockers
    from app.core.form_refresh import form_inventory_blockers
    issues = []
    if page.get("status") != "extracted":
        issues.append("Page extraction is not complete.")
    if not page.get("coverage_approved"):
        issues.append("Whole-page completeness has not been reviewed.")
    issues.extend(table_blockers(page))
    issues.extend(form_inventory_blockers(page))
    for region in page.get("regions", []):
        issues.extend(f"Region {region['order'] + 1}: {message}" for message in region_blockers(region, glossary, target))
    return issues


def checked_revision(actual: int, expected: int):
    if actual != expected:
        raise ReviewError("revision_conflict", "This content changed. Reload before saving; your edit has not been applied.", 409)
