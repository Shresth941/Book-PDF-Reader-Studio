import asyncio

from fastapi import HTTPException

from app.infrastructure import storage


class FakeBlobResult:
    status_code = 200
    content = b"%PDF-1.7\nverification"
    size = len(content)


class FakeBlobClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, url, *, access):
        assert access == "private"
        assert url.startswith("https://")
        return FakeBlobResult()


def test_private_blob_download_uses_sdk_content(monkeypatch):
    monkeypatch.setattr(storage, "AsyncBlobClient", FakeBlobClient)
    documents = storage.VercelBlobDocumentStorage(max_bytes=1024)

    content = asyncio.run(
        documents.read(
            "https://example.private.blob.vercel-storage.com/documents/sample.pdf"
        )
    )

    assert content == FakeBlobResult.content


def test_private_blob_url_rejects_other_hosts():
    documents = storage.VercelBlobDocumentStorage(max_bytes=1024)

    try:
        asyncio.run(documents.read("https://example.com/documents/sample.pdf"))
    except HTTPException as error:
        assert error.status_code == 422
    else:
        raise AssertionError("An external Blob URL should be rejected.")
