from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4
import fitz
from fastapi import HTTPException, UploadFile

try:
    from vercel.blob import AsyncBlobClient
except ImportError:  # Local development does not require a Blob account.
    AsyncBlobClient = None

class LocalDocumentStorage:
    """Private disk adapter; replace with S3/R2 for production."""
    def __init__(self, root: Path, max_bytes: int | None):
        self.root, self.max_bytes = root, max_bytes
        root.mkdir(parents=True, exist_ok=True)

    async def save(self, file: UploadFile) -> tuple[UUID, str, int]:
        filename = Path(file.filename or "document.pdf").name
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(415, "Only PDF files are supported.")
        identifier, path, total = uuid4(), None, 0
        path = self.root / f"{identifier}.pdf"
        try:
            with path.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if self.max_bytes is not None and total > self.max_bytes:
                        raise HTTPException(413, "PDF exceeds the configured upload limit.")
                    output.write(chunk)
            with path.open("rb") as uploaded:
                if uploaded.read(5) != b"%PDF-":
                    raise HTTPException(415, "The uploaded file is not a valid PDF.")
            with fitz.open(path) as pdf:
                pages = pdf.page_count
            if not pages:
                raise HTTPException(422, "The PDF has no pages.")
            return identifier, filename, pages
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def path_for(self, identifier: UUID) -> Path:
        path = self.root / f"{identifier}.pdf"
        if not path.is_file():
            raise HTTPException(404, "Document not found or expired.")
        return path


class VercelBlobDocumentStorage:
    """Read private PDF uploads from Vercel Blob without exposing its token."""

    def __init__(self, max_bytes: int | None):
        self.max_bytes = max_bytes

    @staticmethod
    def _validate_url(blob_url: str) -> None:
        parsed = urlparse(blob_url)
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not hostname.endswith(".private.blob.vercel-storage.com")
            or not parsed.path.startswith("/documents/")
        ):
            raise HTTPException(422, "Invalid private document reference.")

    async def read(self, blob_url: str) -> bytes:
        self._validate_url(blob_url)
        if AsyncBlobClient is None:
            raise HTTPException(503, "Vercel Blob support is not installed.")

        try:
            result = await AsyncBlobClient().get(blob_url, access="private")
        except Exception as error:
            raise HTTPException(502, "Unable to read the private PDF upload.") from error
        if result is None or result.status_code != 200 or result.stream is None:
            raise HTTPException(404, "Document not found or expired.")
        if (
            self.max_bytes is not None
            and result.blob.size is not None
            and result.blob.size > self.max_bytes
        ):
            raise HTTPException(413, "PDF exceeds the configured upload limit.")

        content = bytearray()
        async for chunk in result.stream:
            content.extend(chunk)
            if self.max_bytes is not None and len(content) > self.max_bytes:
                raise HTTPException(413, "PDF exceeds the configured upload limit.")
        if content[:5] != b"%PDF-":
            raise HTTPException(415, "The uploaded file is not a valid PDF.")
        return bytes(content)
