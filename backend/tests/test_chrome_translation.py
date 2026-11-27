import pytest
from app.core.review import ReviewError
from app.services.chrome_translation import chrome_page
from test_review_workflow import source_pdf, workspace


def reviewed(workspace):
    store, doc = workspace
    p = store.page(doc, 1)
    for r in p["regions"]:
        p = store.mutate_region(doc, 1, p["revision"], r["id"], {"source_language": "en", "disposition": "translate"}, "test")
        p = store.approve(doc, 1, p["revision"], "source", "test", r["id"])
    return p


def test_source_required_and_direction_checked(workspace):
    store, doc = workspace
    rev = store.page(doc, 1)["revision"]
    with pytest.raises(ReviewError, match="source passages"):
        chrome_page(store, doc, 1, rev, "en", "hi")
    with pytest.raises(ReviewError, match="languages"):
        chrome_page(store, doc, 1, rev, "hi", "hi")


@pytest.mark.parametrize("fault", ["missing", "blank", "extra", "stale-page", "stale-document"])
def test_invalid_or_superseded_output_never_partially_saves(workspace, fault):
    store, doc = workspace
    page = reviewed(workspace)
    plan = chrome_page(store, doc, 1, page["revision"], "en", "hi")
    translations = {key: "न्यायालय" for key in plan["units"]}
    if fault == "missing":
        translations.pop(next(iter(translations)))
    if fault == "blank":
        translations[next(iter(translations))] = " "
    if fault == "extra":
        translations["unknown"] = "अतिरिक्त"
    with pytest.raises(ReviewError):
        chrome_page(store, doc, 1, page["revision"] - (fault == "stale-page"), "en", "hi",
            document_revision=plan["document_revision"] - (fault == "stale-document"), translations=translations)
    assert store.page(doc, 1)["revision"] == page["revision"]


def test_approved_translation_kept_and_replay_rejected(workspace):
    store, doc = workspace
    page = reviewed(workspace)
    first = page["regions"][0]["id"]
    with store.connection(True) as db:
        old = store._page(db, doc, 1)
        old["regions"][0].update(translation_approved=True, translation="समीक्षित पाठ")
        store._save_page(db, doc, old, "test", {})
    revision = store.page(doc, 1)["revision"]
    plan = chrome_page(store, doc, 1, revision, "en", "hi")
    assert first not in plan["units"]
    result = {k: "यह अलग खंड है।" for k in plan["units"]}
    args = dict(document_revision=plan["document_revision"], translations=result)
    saved = chrome_page(store, doc, 1, revision, "en", "hi", **args)
    assert saved["regions"][0]["translation"] == "समीक्षित पाठ"
    assert saved["regions"][0]["translation_approved"]
    assert all(not r["translation_approved"] for r in saved["regions"][1:])
    with pytest.raises(ReviewError):
        chrome_page(store, doc, 1, revision, "en", "hi", **args)


def test_constitution_contents_keep_reference_and_layout(workspace):
    from app.services.constitution_reference import HINDI_SHA
    store, doc = workspace
    with store.connection(True) as db:
        db.execute("UPDATE documents SET target='en',domain='legal',sha256=? WHERE id=?", (HINDI_SHA, doc))
        page = store._page(db, doc, 1)
        page["regions"] = page["regions"][:1]
        page["regions"][0].update(source="143. उच्चतम न्यायालय से परामर्श करने की राष्ट्रपति की शक्ति .... 80\n144क. [निरसित।] .... 81",
                                  source_approved=True, source_language="hi", disposition="translate")
        store._save_page(db, doc, page, "test", {})
    rev = store.page(doc, 1)["revision"]
    plan = chrome_page(store, doc, 1, rev, "hi", "en")
    assert plan["units"] == {}
    page = chrome_page(store, doc, 1, rev, "hi", "en", document_revision=plan["document_revision"], translations={})
    rows = page["regions"][0]["translation_layout"]["rows"]
    assert [(r["label"], r["text"], r["page"]) for r in rows] == [
        ("143.", "Power of President to consult Supreme Court", "80"), ("144A.", "[Repealed.]", "81")]
