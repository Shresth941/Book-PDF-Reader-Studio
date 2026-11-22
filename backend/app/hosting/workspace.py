"""Request-owned SQLite snapshots published with an optimistic Blob transaction.

Nothing under /tmp is authoritative. A successful mutation is returned only after
its immutable files and conditional state publication finish. Conflicts never
replace a newer review. Local ReviewStore remains unchanged.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
import zlib

from app.core.review import ReviewError
from app.infrastructure.review_store import ReviewStore, encode
from app.hosting.auth import protect_passwords, unprotect_passwords
from app.hosting.blob_storage import BlobStorage, BlobConflict

STATE_KEY = "pdf-review/v1/workspace.json"
MAX_DATABASE = 64 * 1024**2
MAX_TEMP_FILES = 240 * 1024**2


class HostedWorkspace:
    def __init__(self):
        self.blob = BlobStorage()
        self.temp = tempfile.TemporaryDirectory(prefix="pdf-review-")
        self.root = Path(self.temp.name)
        self.files = {}
        self.passwords = {}
        self.state = {}
        self.etag = None
        self.loaded = set()

    def __enter__(self):
        try:
            saved = self.blob.get_bytes(STATE_KEY)
            if saved:
                self.etag = saved.etag
                self.state = json.loads(saved.data)
                if self.state.get("version") != 1:
                    raise ReviewError("workspace_version", "This workspace requires a newer server version.", 503)
                self.files = self.state["files"]
                self.passwords = unprotect_passwords(self.state.get("passwords", ""))
                decoder = zlib.decompressobj()
                raw = decoder.decompress(base64.b64decode(self.state["database"], validate=True), MAX_DATABASE + 1)
                if len(raw) > MAX_DATABASE or not decoder.eof:
                    raise ReviewError("workspace_limit", "Workspace metadata exceeds the hosted safety limit. No data was changed.", 507)
                (self.root / "review.sqlite3").write_bytes(raw)
            self.store = ReviewStore(self.root)
            self.rebase_paths(local=True)
            self.before = self.database_bytes()
            self.before_passwords = dict(self.passwords)
            return self
        except BaseException:
            self.temp.cleanup()
            raise

    def __exit__(self, *_):
        self.temp.cleanup()

    def path(self, relative: str) -> Path:
        item = PurePosixPath(relative)
        if item.is_absolute() or ".." in item.parts or "\\" in relative or not item.parts or item.parts[0] not in ("originals", "artifacts", "backups"):
            raise ReviewError("artifact_path", "Invalid workspace artifact location.", 500)
        return self.root.joinpath(*item.parts)

    def ensure(self, relative: str) -> Path:
        target = self.path(relative)
        if target.is_file():
            return target
        info = self.files.get(relative)
        if not info:
            return target
        used = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
        if used + info["size"] > MAX_TEMP_FILES:
            raise ReviewError("hosted_workspace_limit", "This operation exceeds Vercel's temporary storage budget. Download individual documents or use the local workspace for larger operations.", 507)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not self.blob.download_file(info["key"], target, max_bytes=info["size"]):
            raise ReviewError("artifact_missing", "A saved artifact is unavailable. Retry when storage is available; the document has not been replaced.", 503)
        with target.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if target.stat().st_size != info["size"] or actual != info["sha256"]:
            target.unlink(missing_ok=True)
            raise ReviewError("artifact_integrity", "Artifact integrity verification failed. Processing stopped.", 409)
        self.loaded.add(relative)
        return target

    def hydrate_for(self, url_path: str, method: str):
        parts = url_path.strip("/").split("/")
        if len(parts) >= 4 and parts[2] == "documents":
            identifier = parts[3]
            heavy = (method == "POST" and parts[-1] in ("inspect", "exports", "preview", "apply")) or parts[-1] in ("original", "image")
            if heavy:
                self.ensure(f"originals/{identifier}.pdf")
            if len(parts) >= 7 and parts[4] == "pages" and parts[-1] == "image":
                self.ensure(f"artifacts/{identifier}-source-{parts[5]}.png")
        if len(parts) >= 4 and parts[2] == "exports" and parts[-1] in ("download", "image", "approvals"):
            with self.store.connection() as db:
                row = db.execute("SELECT format FROM exports WHERE id=?", (parts[3],)).fetchone()
            if row:
                self.ensure(f"artifacts/{parts[3]}.{row[0]}")
            if parts[-1] == "image":
                self.ensure(f"artifacts/{parts[3]}-preview-{parts[-2]}.png")
        if len(parts) >= 3 and parts[2] == "backups":
            if method == "POST":
                for relative in self.files:
                    if not relative.startswith("backups/"):
                        self.ensure(relative)
            elif len(parts) == 4:
                self.ensure(f"backups/{parts[3]}.zip")

    def rebase_paths(self, local: bool):
        with self.store.connection(True) as db:
            for row in db.execute("SELECT id,format,body FROM exports").fetchall():
                body = json.loads(row["body"])
                relative = f"artifacts/{row['id']}.{row['format']}"
                body["path"] = str(self.path(relative)) if local else relative
                db.execute("UPDATE exports SET body=? WHERE id=?", (encode(body), row["id"]))

    def database_bytes(self):
        # SQLite backup includes committed WAL frames without racing a checkpoint.
        with self.store.connection() as source:
            target = sqlite3.connect(":memory:")
            try:
                source.backup(target)
                raw = target.serialize()
            finally:
                target.close()
        if len(raw) > MAX_DATABASE:
            raise ReviewError("workspace_limit", "Workspace metadata exceeds the hosted safety limit. The existing saved workspace is intact.", 507)
        return raw

    def commit(self):
        current = self.database_bytes()
        changed = current != self.before or self.passwords != self.before_passwords
        updated = dict(self.files)
        for directory in ("originals", "artifacts", "backups"):
            for path in (self.root / directory).rglob("*"):
                if not path.is_file():
                    continue
                relative = path.relative_to(self.root).as_posix()
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                old = self.files.get(relative)
                if old and old["sha256"] == digest:
                    continue
                size = path.stat().st_size
                key = f"pdf-review/v1/objects/{digest}"
                try:
                    self.blob.upload_file(key, path)
                except BlobConflict:
                    # Names are the digest of immutable bytes, so an existing
                    # object under this key is shared, never overwritten.
                    if not self.blob.head(key):
                        raise
                updated[relative] = {"key": key, "sha256": digest, "size": size}
                changed = True
        if not changed:
            return False
        self.rebase_paths(local=False)
        database = base64.b64encode(zlib.compress(self.database_bytes(), level=6)).decode()
        state = {"version": 1, "database": database, "files": updated,
                 "passwords": protect_passwords(self.passwords), "generation": self.state.get("generation", 0) + 1}
        data = json.dumps(state, separators=(",", ":")).encode()
        try:
            if self.etag:
                published = self.blob.put_bytes(STATE_KEY, data, expected_etag=self.etag, content_type="application/json")
            else:
                published = self.blob.create_bytes(STATE_KEY, data, content_type="application/json")
        except BlobConflict as exc:
            raise ReviewError("workspace_conflict", "Another request saved newer work. Reload and retry this action; your browser draft is retained.", 409) from exc
        self.files, self.state, self.etag = updated, state, published.etag
        return True
