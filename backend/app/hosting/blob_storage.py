"""Private, bounded Vercel Blob storage with optimistic conditional writes.

Wire protocol source: the official @vercel/blob 2.8.0 package, specifically
src/{api,helpers,get,head,del,put,signed-token,presign-query-params}.ts:
https://github.com/vercel/storage/tree/main/packages/blob/src
This intentionally uses the confirmed HTTP protocol: vercel Python 0.11.4 does
not expose the conditional overwrite required by the workspace transaction.

All methods are synchronous; asynchronous routes must call them in a thread.
Metadata is bounded separately from streamed artifacts. No request is retried:
an uncertain write outcome must be reconciled by reloading authoritative state.
Signed URLs are short-lived bearer capabilities and must never be logged.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import BinaryIO, Iterator
from urllib.parse import quote, unquote, urlencode, urlsplit
import uuid

import httpx

from app.core.review import ReviewError


API_BASE = "https://vercel.com/api/blob"
API_VERSION = "12"
MAX_METADATA_BYTES = 20 * 1024**2
MAX_ARTIFACT_BYTES = 500 * 1024**2
MAX_API_RESPONSE_BYTES = 1024**2
CHUNK_BYTES = 64 * 1024
TRANSFER_SECONDS = 180
TIMEOUT = httpx.Timeout(connect=10, read=60, write=60, pool=10)
_SAFE_PATH = re.compile(r"[A-Za-z0-9._/-]+\Z")
_CONTROLS = re.compile(r"[\x00-\x1f\x7f]")


class BlobStorageError(ReviewError):
    """A sanitized storage failure, safe for the existing API error handler."""

    def __init__(self, code: str, message: str, status: int = 503, *, provider_status: int | None = None):
        super().__init__(code, message, status)
        self.provider_status = provider_status


class BlobConflict(BlobStorageError):
    """The object exists already or its ETag no longer matches."""

    def __init__(self):
        super().__init__("blob_conflict", "Stored content changed or already exists. Reload before retrying this action.", 409)


@dataclass(frozen=True)
class BlobInfo:
    pathname: str
    url: str
    etag: str
    size: int
    content_type: str


@dataclass(frozen=True)
class BlobBytes(BlobInfo):
    data: bytes


class BlobStorage:
    """Own-store private storage; inputs are generated object paths, never URLs."""

    def __init__(self, token: str | None = None):
        value = token if token is not None else (
            os.environ.get("REVIEW_BLOB_READ_WRITE_TOKEN") or os.environ.get("BLOB_READ_WRITE_TOKEN")
        )
        parts = value.split("_") if isinstance(value, str) else []
        if len(parts) < 5 or parts[:3] != ["vercel", "blob", "rw"] or not re.fullmatch(r"[A-Za-z0-9]+", parts[3]) or _CONTROLS.search(value):
            raise BlobStorageError("blob_configuration", "Configure a valid private Vercel Blob read-write token on the server.")
        self._token = value
        self.store_id = parts[3]
        self._host = f"{self.store_id.lower()}.private.blob.vercel-storage.com"

    @staticmethod
    def _path(path: str) -> str:
        # Workspace keys are UUIDs/digests, not uploaded filenames. Keeping this
        # alphabet narrow also avoids ambiguous URL decoding and traversal.
        if not isinstance(path, str) or not path or len(path) > 950 or not _SAFE_PATH.fullmatch(path) or any(part in ("", ".", "..") for part in path.split("/")):
            raise BlobStorageError("blob_path", "Invalid generated storage object path.", 422)
        return path

    @staticmethod
    def _etag(value: str) -> str:
        if not isinstance(value, str) or not value or value == "*" or len(value) > 256 or _CONTROLS.search(value) or value.startswith("W/"):
            raise BlobStorageError("blob_etag", "Storage did not provide a usable object revision. No unconditional overwrite is allowed.", 502)
        return value

    @staticmethod
    def _content_type(value: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 256 or _CONTROLS.search(value):
            raise BlobStorageError("blob_content_type", "Invalid storage content type.", 422)
        return value

    @staticmethod
    def _limit(value: int, maximum: int) -> int:
        if type(value) is not int or not 0 <= value <= maximum:
            raise BlobStorageError("blob_size_limit", "The requested storage operation exceeds its configured size limit.", 413)
        return value

    def _url(self, path: str) -> str:
        return f"https://{self._host}/{quote(self._path(path), safe='/._-')}"

    def _validate_url(self, value: object, path: str) -> str:
        if not isinstance(value, str):
            raise BlobStorageError("blob_protocol", "Storage returned an invalid private object address.", 502)
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.netloc.lower() != self._host or parsed.query or parsed.fragment or unquote(parsed.path) != "/" + path:
            raise BlobStorageError("blob_protocol", "Storage returned an object outside this private workspace.", 502)
        return value

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._token}",
            "x-api-version": API_VERSION,
            "x-vercel-blob-store-id": self.store_id,
            "x-api-blob-request-id": f"{self.store_id}:{int(time.time() * 1000)}:{uuid.uuid4().hex}",
            "x-api-blob-request-attempt": "0",
            "accept-encoding": "identity",
        }

    @staticmethod
    def _client() -> httpx.Client:
        # No redirects (and therefore no credential forwarding to another host).
        # Ignore ambient proxy/netrc configuration for this credentialed client.
        return httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False)

    @staticmethod
    def _read_bounded(response: httpx.Response, limit: int) -> bytes:
        data = bytearray()
        deadline = time.monotonic() + TRANSFER_SECONDS
        for chunk in response.iter_bytes(CHUNK_BYTES):
            if time.monotonic() > deadline:
                raise BlobStorageError("blob_timeout", "Storage transfer exceeded the time limit. Reload before retrying.")
            if len(data) + len(chunk) > limit:
                raise BlobStorageError("blob_size_limit", "Stored data exceeds the configured safe size limit.", 413)
            data.extend(chunk)
        return bytes(data)

    @staticmethod
    def _raise_status(status: int, body: bytes):
        # Never forward provider response bodies: they can contain signed URLs,
        # pathname details, or credential diagnostics.
        try:
            payload = json.loads(body)
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = error.get("code") if isinstance(error, dict) else None
        except (ValueError, UnicodeError):
            code = None
        if status in (409, 412) or code == "precondition_failed":
            raise BlobConflict()
        if status == 429 or code in ("rate_limited", "store_suspended"):
            raise BlobStorageError("blob_unavailable", "Private storage is currently unavailable or its quota has been reached. Saved work has not been replaced.", 503, provider_status=status)
        if status in (401, 403) or code in ("forbidden", "store_not_found"):
            raise BlobStorageError("blob_configuration", "Private storage credentials or store access require server configuration.", 503, provider_status=status)
        raise BlobStorageError("blob_request", "Private storage could not complete this request. Reload before retrying an uncertain write.", 502 if status < 500 else 503, provider_status=status)

    def _request_json(self, method: str, suffix: str = "", *, headers: dict[str, str] | None = None, content=None, payload=None, missing_ok: bool = False) -> dict | None:
        request_headers = self._headers()
        request_headers.update(headers or {})
        kwargs = {"headers": request_headers}
        if content is not None:
            kwargs["content"] = content
        if payload is not None:
            kwargs["json"] = payload
        try:
            with self._client() as client, client.stream(method, API_BASE + suffix, **kwargs) as response:
                if response.status_code == 404 and missing_ok:
                    return None
                raw = self._read_bounded(response, MAX_API_RESPONSE_BYTES)
                if not 200 <= response.status_code < 300:
                    self._raise_status(response.status_code, raw)
                if not raw:
                    return {}
                try:
                    data = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise BlobStorageError("blob_protocol", "Storage returned an unreadable response.", 502) from None
                if not isinstance(data, dict):
                    raise BlobStorageError("blob_protocol", "Storage returned an invalid response.", 502)
                return data
        except httpx.HTTPError:
            raise BlobStorageError("blob_transport", "Storage connection failed or timed out. Reload before retrying an uncertain write.") from None

    def _info(self, path: str, data: dict, *, size: int | None = None) -> BlobInfo:
        if data.get("pathname") != path:
            raise BlobStorageError("blob_protocol", "Storage returned a different object path.", 502)
        url = self._validate_url(data.get("url"), path)
        etag = self._etag(data.get("etag"))
        actual_size = data.get("size", size)
        if type(actual_size) is not int or actual_size < 0 or (size is not None and actual_size != size):
            raise BlobStorageError("blob_protocol", "Storage returned an inconsistent object size.", 502)
        return BlobInfo(path, url, etag, actual_size, self._content_type(data.get("contentType", "application/octet-stream")))

    def get_bytes(self, path: str) -> BlobBytes | None:
        """Read current metadata and ETag; never use the private edge cache."""
        path = self._path(path)
        url = self._url(path)
        try:
            with self._client() as client, client.stream("GET", url + "?cache=0", headers=self._headers()) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    self._raise_status(response.status_code, self._read_bounded(response, MAX_API_RESPONSE_BYTES))
                etag = self._etag(response.headers.get("etag"))
                self._check_length(response, MAX_METADATA_BYTES)
                data = self._read_bounded(response, MAX_METADATA_BYTES)
                self._check_complete(response, len(data))
                return BlobBytes(path, url, etag, len(data), response.headers.get("content-type", "application/octet-stream"), data)
        except httpx.HTTPError:
            raise BlobStorageError("blob_transport", "Private storage could not be read. No saved state was changed.") from None

    @staticmethod
    def _check_length(response: httpx.Response, limit: int):
        value = response.headers.get("content-length")
        if value is not None:
            if not value.isdecimal():
                raise BlobStorageError("blob_protocol", "Storage returned an invalid object length.", 502)
            if int(value) > limit:
                raise BlobStorageError("blob_size_limit", "Stored data exceeds the configured safe size limit.", 413)

    @staticmethod
    def _check_complete(response: httpx.Response, actual: int):
        value = response.headers.get("content-length")
        if value is not None and int(value) != actual:
            raise BlobStorageError("blob_incomplete", "Storage returned incomplete object bytes. The download was not published.", 502)

    def _put(self, path: str, content, size: int, content_type: str, expected_etag: str | None) -> BlobInfo:
        path = self._path(path)
        headers = {
            "x-vercel-blob-access": "private",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1" if expected_etag is not None else "0",
            "x-content-type": self._content_type(content_type),
            "content-type": content_type,
            "content-length": str(size),
        }
        if expected_etag is not None:
            headers["x-if-match"] = self._etag(expected_etag)
        try:
            result = self._request_json("PUT", "/?" + urlencode({"pathname": path}), headers=headers, content=content)
        except BlobStorageError as exc:
            if expected_etag is None and exc.provider_status == 400:
                # The official SDK exposes existing-name failures as a generic
                # bad_request. Confirm existence instead of matching prose or
                # misclassifying every 400 response as a successful dedup.
                try:
                    existing = self.head(path)
                except BlobStorageError:
                    existing = None
                if existing is not None:
                    raise BlobConflict() from None
            raise
        return self._info(path, result, size=size)

    def create_bytes(self, path: str, data: bytes, *, content_type: str = "application/octet-stream") -> BlobInfo:
        """Create metadata once; an existing pathname cannot be overwritten."""
        if not isinstance(data, bytes):
            raise BlobStorageError("blob_data", "Metadata must be bytes.", 422)
        self._limit(len(data), MAX_METADATA_BYTES)
        return self._put(path, data, len(data), content_type, None)

    def put_bytes(self, path: str, data: bytes, *, expected_etag: str, content_type: str = "application/octet-stream") -> BlobInfo:
        """Compare-and-swap metadata. The expected ETag is mandatory."""
        self._etag(expected_etag)
        if not isinstance(data, bytes):
            raise BlobStorageError("blob_data", "Metadata must be bytes.", 422)
        self._limit(len(data), MAX_METADATA_BYTES)
        return self._put(path, data, len(data), content_type, expected_etag)

    @staticmethod
    def _file_chunks(stream: BinaryIO, expected_size: int) -> Iterator[bytes]:
        sent = 0
        deadline = time.monotonic() + TRANSFER_SECONDS
        while chunk := stream.read(CHUNK_BYTES):
            if time.monotonic() > deadline:
                raise BlobStorageError("blob_timeout", "Storage transfer exceeded the time limit. Reload before retrying.")
            sent += len(chunk)
            if sent > expected_size:
                raise BlobStorageError("blob_file_changed", "The local artifact changed during upload.", 409)
            yield chunk
        if sent != expected_size:
            raise BlobStorageError("blob_file_changed", "The local artifact changed during upload.", 409)

    def upload_file(self, path: str, source: str | Path, *, expected_etag: str | None = None, content_type: str = "application/octet-stream") -> BlobInfo:
        """Stream an artifact. None means create-only, never unconditional PUT."""
        self._path(path)
        try:
            with Path(source).open("rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                self._limit(size, MAX_ARTIFACT_BYTES)
                return self._put(path, self._file_chunks(stream, size), size, content_type, expected_etag)
        except OSError:
            raise BlobStorageError("blob_local_io", "The local artifact could not be read for upload.", 507) from None

    def download_file(self, path: str, destination: str | Path, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> BlobInfo | None:
        """Stream to a sibling temporary file; publish only a complete download."""
        path = self._path(path)
        self._limit(max_bytes, MAX_ARTIFACT_BYTES)
        url = self._url(path)
        target = Path(destination)
        temporary = None
        try:
            with self._client() as client, client.stream("GET", url + "?cache=0", headers=self._headers()) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    self._raise_status(response.status_code, self._read_bounded(response, MAX_API_RESPONSE_BYTES))
                self._check_length(response, max_bytes)
                etag = self._etag(response.headers.get("etag"))
                target.parent.mkdir(parents=True, exist_ok=True)
                size = 0
                deadline = time.monotonic() + TRANSFER_SECONDS
                with tempfile.NamedTemporaryFile(prefix=".blob-", dir=target.parent, delete=False) as output:
                    temporary = Path(output.name)
                    for chunk in response.iter_bytes(CHUNK_BYTES):
                        if time.monotonic() > deadline:
                            raise BlobStorageError("blob_timeout", "Storage transfer exceeded the time limit.")
                        size += len(chunk)
                        if size > max_bytes:
                            raise BlobStorageError("blob_size_limit", "Stored artifact exceeds the configured safe size limit.", 413)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                self._check_complete(response, size)
                temporary.replace(target)
                temporary = None
                return BlobInfo(path, url, etag, size, response.headers.get("content-type", "application/octet-stream"))
        except httpx.HTTPError:
            raise BlobStorageError("blob_transport", "The artifact download failed. An incomplete file was not published.") from None
        except OSError:
            raise BlobStorageError("blob_local_io", "The artifact could not be saved to temporary disk.", 507) from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def head(self, path: str) -> BlobInfo | None:
        path = self._path(path)
        result = self._request_json("GET", "?" + urlencode({"url": self._url(path)}), missing_ok=True)
        return None if result is None else self._info(path, result)

    def delete(self, path: str, *, expected_etag: str | None = None) -> None:
        path = self._path(path)
        headers = {"x-if-match": self._etag(expected_etag)} if expected_etag is not None else {}
        self._request_json("POST", "/delete", headers=headers, payload={"urls": [self._url(path)]})

    def _signed_url(self, path: str, operation: str, expires_seconds: int, *, max_bytes: int | None = None, content_type: str | None = None) -> str:
        path = self._path(path)
        if type(expires_seconds) is not int or not 1 <= expires_seconds <= 3600:
            raise BlobStorageError("blob_expiry", "Storage link expiry must be between one second and one hour.", 422)
        until = int(time.time() * 1000) + expires_seconds * 1000
        request = {"pathname": path, "operations": [operation], "validUntil": until}
        if operation == "put":
            self._limit(max_bytes, MAX_ARTIFACT_BYTES)
            self._content_type(content_type)
            request.update(maximumSizeInBytes=max_bytes, allowedContentTypes=[content_type])
        issued = self._request_json("POST", "/signed-token", payload=request)
        delegation = issued.get("delegationToken")
        signing_key = issued.get("clientSigningToken")
        try:
            if not isinstance(delegation, str) or not isinstance(signing_key, str) or not signing_key or len(delegation) > 65536 or len(signing_key) > 65536:
                raise ValueError
            encoded = delegation.split(".", 1)[0]
            scope = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            store = scope.get("storeId", "")
            store = store.removeprefix("store_") if isinstance(store, str) else ""
            scope_until = scope.get("validUntil")
            if store.lower() != self.store_id.lower() or scope.get("pathname") != path or scope.get("operations") != [operation] or type(scope_until) is not int or not int(time.time() * 1000) < scope_until <= until:
                raise ValueError
            if operation == "put" and (scope.get("maximumSizeInBytes") != max_bytes or scope.get("allowedContentTypes") != [content_type]):
                raise ValueError
        except (ValueError, TypeError, AttributeError, UnicodeError):
            raise BlobStorageError("blob_protocol", "Storage did not issue the requested restricted capability.", 502) from None

        # Matches SDK canonicalString: UTF-8 byte sorting, newline-separated
        # operation/pathname/constraint lines, no trailing newline. The expiry
        # already equals the delegation expiry, so no narrower URL expiry entry.
        params = {}
        if operation == "put":
            params = {
                "vercel-blob-add-random-suffix": "false",
                "vercel-blob-allow-overwrite": "false",
                "vercel-blob-allowed-content-types": content_type,
                "vercel-blob-maximum-size-in-bytes": str(max_bytes),
            }
        lines = [f"operation={operation}", f"pathname={path}"] + [f"{key}={value}" for key, value in params.items()]
        canonical = "\n".join(sorted(lines, key=lambda line: line.encode("utf-8")))
        digest = hmac.new(signing_key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).digest()
        params.update({"vercel-blob-delegation": delegation, "vercel-blob-signature": base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")})
        if operation == "get":
            return self._url(path) + "?" + urlencode({"cache": "0", **params})
        return API_BASE + "/?" + urlencode({"pathname": path, **params})

    def signed_download(self, path: str, expires_seconds: int = 300) -> str:
        """Return an own-object GET-only capability, without the server RW token."""
        return self._signed_url(path, "get", expires_seconds)

    def signed_upload(self, path: str, expires_seconds: int = 600, *, max_bytes: int = 64 * 1024**2, content_type: str = "application/pdf") -> dict:
        """Direct create-only PUT, bounded by exact path, size, type and expiry.

        Browser usage: fetch(result.url, {method: 'PUT', headers: result.headers,
        body: file}). The authorized route must independently verify the stored
        file before attaching it to a document; a successful upload is not import.
        """
        url = self._signed_url(path, "put", expires_seconds, max_bytes=max_bytes, content_type=content_type)
        return {"url": url, "headers": {
            "x-api-version": API_VERSION,
            "x-vercel-blob-store-id": self.store_id,
            "x-vercel-blob-access": "private",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "0",
            "x-content-type": content_type,
            "content-type": content_type,
        }}
