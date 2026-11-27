"""Plan and commit a locally translated page using the same legal and layout rules as the worker."""
from app.core.review import ReviewError, checked_revision
from app.services.local_translation import translate_segments


def chrome_page(store, identifier, number, revision, source, target, *, document_revision=None, translations=None):
    applying = translations is not None
    with store.connection(applying) as db:
        doc = store._document(db, identifier)
        page = store._page(db, identifier, number)
        checked_revision(page["revision"], revision)
        if applying:
            checked_revision(doc["revision"], document_revision)
        if doc["target"] != target or source == target:
            raise ReviewError("language_changed", "Check the source and target languages again.", 409)
        regions = [r for r in page["regions"] if r["disposition"] == "translate" and not r["translation_approved"]]
        if any(not r["source_approved"] or r["source_language"] != source for r in regions):
            raise ReviewError("source_unreviewed", "Check all source passages before translating.")
        units = {}

        def from_browser(_models, pending, _target):
            results = []
            for r in pending:
                key = r["id"]
                units[key] = r["source"]
                value = translations.get(key, "") if applying else r["source"]
                if not value.strip():
                    raise ReviewError("incomplete_translation", "An empty or missing passage prevented saving this page.")
                results.append({"id": key, "translation": value, "model": "chrome-on-device",
                                "alignment": [], "draft_only": True, "quality_qualified": False})
            return {"regions": results}

        result = translate_segments(None, regions, target, domain=doc["domain"], jurisdiction=doc["jurisdiction"],
                                    glossary=store._glossary(db, identifier), document_sha=doc["sha256"],
                                    page_number=number, model_translate=from_browser)
        if not applying:
            return {"document_revision": doc["revision"], "revision": revision, "units": units}
        if translations.keys() != units.keys():
            raise ReviewError("unexpected_translation", "The returned passages differ from the translation plan.", 409)
        by_id = {r["id"]: r for r in result["regions"]}
        for region in regions:
            for field in ("translation_layout", "contents_models", "model_revision", "reference_page",
                          "terminology_adjustments", "translation_style", "cloud_request"):
                region.pop(field, None)
            region.update(by_id[region["id"]], translation_approved=False, acknowledged={})
        if regions:
            page["coverage_approved"] = False
            store._save_page(db, identifier, page, "chrome_translation", {"source": source, "target": target, "units": len(units)})
    return store.page(identifier, number)
