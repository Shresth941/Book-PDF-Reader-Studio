import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.api.review_routes import GoogleComparisonRequest
from app.core.config import Settings
from app.core.review import ReviewError
from app.services.google_cloud_translation import compare
from test_review_workflow import source_pdf, workspace


@pytest.mark.parametrize("source,target,text,result", [
    ("en", "hi", "The court sits on Monday.", "सोमवार को न्यायालय की बैठक होती है।"),
    ("hi", "en", "न्यायालय सोमवार को बैठेगा।", "The court will sit on Monday."),
])
def test_cloud_comparison_contract(workspace, source, target, text, result):
    store, doc = workspace
    p = store.page(doc, 1)
    rid = p["regions"][0]["id"]
    p = store.mutate_region(doc, 1, p["revision"], rid, {"source": text}, "QA")
    def google(request):
        assert request.url == "https://translation.googleapis.com/language/translate/v2"
        assert request.headers["x-goog-api-key"] == "mock-test-key"
        assert json.loads(request.content) == {"q": [text], "format": "text", "source": source, "target": target}
        return httpx.Response(200, json={"data": {"translations": [{"translatedText": result}]}})
    answer = asyncio.run(compare(Settings(google_translate_api_key="mock-test-key"), store, doc, 1, rid,
        p["revision"], text, source, target, transport=httpx.MockTransport(google)))
    assert answer["translation"] == result
    assert store.page(doc, 1)["revision"] == p["revision"]


@pytest.mark.parametrize("fault", ["disabled", "hosted", "stale", "different text", "same language", "quota", "denied", "timeout", "blank", "malformed"])
def test_cloud_failures(workspace, fault):
    store, doc = workspace
    p = store.page(doc, 1)
    region = p["regions"][0]
    sent = []
    def provider(request):
        sent.append(True)
        if fault == "timeout":
            raise httpx.ReadTimeout("timeout")
        return httpx.Response({"denied":403, "quota":429}.get(fault,200),
            json={"data":{"translations":[{"translatedText":" "}] if fault == "blank" else []}})
    config = Settings(google_translate_api_key=None if fault == "disabled" else "mock-test-key",
                      runtime_mode="hosted" if fault == "hosted" else "local")
    with pytest.raises(ReviewError):
        asyncio.run(compare(config, store, doc, 1, region["id"], p["revision"]-(fault == "stale"),
            "changed" if fault == "different text" else region["source"], "en", "en" if fault == "same language" else "hi",
            transport=httpx.MockTransport(provider)))
    assert len(sent) == int(fault in {"quota", "denied", "timeout", "blank", "malformed"})
    assert store.page(doc, 1)["revision"] == p["revision"]


def test_exact_consent_required():
    request = dict(revision=1, source="en", target="hi", text="public text", public_nonpersonal=True)
    for consent in ({}, {"approve_send":False}, {"approve_send":True, "public_nonpersonal":False}):
        with pytest.raises(ValidationError):
            GoogleComparisonRequest(**(request | consent))
