import asyncio
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.core.review import ReviewError
from app.services.approved_cloud import ApprovedCloud
from test_review_workflow import workspace, source_pdf, approve_all


def configured():
    return SimpleNamespace(gemini_api_key=SecretStr("test-key-not-real"), google_readonly_access_token=SecretStr("test-token-not-real"),
                           google_project_number="12345", cloud_policy_reviewed_on=date.today().isoformat())


@pytest.mark.parametrize("billing", [{"billingEnabled": True, "billingAccountName": "billingAccounts/1"}, {}, {"billingEnabled": False}])
def test_cloud_requires_explicit_empty_billing_account(workspace, billing):
    store, _ = workspace
    service = ApprovedCloud(store, configured())
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"parent": "projects/12345/locations/global", "name": "keys/1"} if "lookupKey" in str(request.url) else billing)
    async def verify():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await service.verify_unbilled(client)
    with pytest.raises(ReviewError, match="billed"):
        asyncio.run(verify())
    assert len(requests) == 2
    assert all("generativelanguage" not in str(r.url) for r in requests)


def test_cloud_mismatched_owner_fails_before_billing_or_content(workspace):
    store, _ = workspace
    service = ApprovedCloud(store, configured())
    async def verify():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"parent": "projects/other", "name": "keys/1"}))) as client:
            await service.verify_unbilled(client)
    with pytest.raises(ReviewError, match="ownership"):
        asyncio.run(verify())


def test_changed_payload_and_private_content_cannot_be_sent(workspace):
    store, identifier = workspace
    approve_all(store, identifier)
    service = ApprovedCloud(store, configured())
    page = store.page(identifier, 1)
    prepared = service.prepare(identifier, 1, store.document(identifier)["revision"], [page["regions"][0]["id"]])
    with pytest.raises(ReviewError, match="match"):
        asyncio.run(service.approve(prepared["id"], "0" * 64, True, "tester"))
    with pytest.raises(ReviewError, match="local"):
        asyncio.run(service.approve(prepared["id"], prepared["payload_sha256"], False, "tester"))


def test_cloud_approval_invalidated_by_source_edit(workspace):
    store, identifier = workspace
    approve_all(store, identifier)
    service = ApprovedCloud(store, configured())
    page = store.page(identifier, 1)
    prepared = service.prepare(identifier, 1, store.document(identifier)["revision"], [page["regions"][0]["id"]])
    store.mutate_region(identifier, 1, page["revision"], page["regions"][0]["id"], {"source": "New source"}, "tester")
    with pytest.raises(ReviewError, match="changed"):
        asyncio.run(service.approve(prepared["id"], prepared["payload_sha256"], True, "tester"))
