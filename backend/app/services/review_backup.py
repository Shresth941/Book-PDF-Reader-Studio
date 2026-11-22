"""Consistent backups and verified restore into a NEW directory (never over live data)."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from uuid import uuid4
import zipfile
from contextlib import closing

from app.core.review import ReviewError


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def create_backup(store):
    target = store.root / "backups" / f"{uuid4()}.zip"
    try:
        with tempfile.TemporaryDirectory(dir=store.root) as directory, store.connection(True) as lock:
            # Hold a writer reservation, but run SQLite backup from an independent read connection.
            temporary = Path(directory) / "review.sqlite3"
            with closing(sqlite3.connect(store.path)) as source, closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination)
            files = {"review.sqlite3": temporary}
            for row in lock.execute("SELECT id FROM documents"):
                files[f"originals/{row[0]}.pdf"] = store.root / "originals" / f"{row[0]}.pdf"
            for row in lock.execute("SELECT body FROM exports"):
                artifact = Path(json.loads(row[0])["path"])
                files["artifacts/" + artifact.name] = artifact
            manifest = {name: digest(path) for name, path in files.items()}
            with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, path in files.items():
                    archive.write(path, name)
                archive.writestr("manifest.json", json.dumps({"schema": 1, "files": manifest}))
        return target
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def restore_backup(archive_path: Path, destination: Path):
    destination = destination.resolve()
    if destination.exists():
        raise ReviewError("restore_target_exists", "Restore into a new, empty directory. Existing data is never overwritten.")
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or "manifest.json" not in names:
            raise ReviewError("invalid_backup", "Backup has duplicate files or no manifest.")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema") != 1 or set(names) != set(manifest.get("files", {})) | {"manifest.json"}:
            raise ReviewError("invalid_backup", "Backup manifest does not match its contents.")
        total = sum(info.file_size for info in archive.infolist())
        destination.parent.mkdir(parents=True, exist_ok=True)
        if total > shutil.disk_usage(destination.parent).free - 128 * 1024**2:
            raise ReviewError("disk_full", "Not enough free disk space to restore this backup.")
        for name in names:
            if not (destination / name).resolve().is_relative_to(destination) or "\\" in name or ":" in name:
                raise ReviewError("invalid_backup", "Backup contains an unsafe path.")
        with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
            # Extract and rewrite inside an owned directory on the destination
            # volume. A failed restore must never expose a half-copied database
            # at the requested workspace path.
            temp = Path(staging) / "workspace"
            temp.mkdir()
            for name, expected in manifest["files"].items():
                path = temp / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, path.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                if digest(path) != expected:
                    raise ReviewError("invalid_backup", "Backup checksum verification failed.")
            with closing(sqlite3.connect(temp / "review.sqlite3")) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ReviewError("invalid_backup", "Backup database integrity check failed.")
                for identifier, sha in db.execute("SELECT id,sha256 FROM documents"):
                    if digest(temp / "originals" / f"{identifier}.pdf") != sha:
                        raise ReviewError("invalid_backup", "An original document does not match its stored fingerprint.")
                for identifier, raw in db.execute("SELECT id,body FROM exports").fetchall():
                    body = json.loads(raw)
                    body["path"] = str(destination / "artifacts" / Path(body["path"]).name)
                    db.execute("UPDATE exports SET body=? WHERE id=?", (json.dumps(body, ensure_ascii=False), identifier))
                db.execute("UPDATE jobs SET status='paused',attempt=attempt+1 WHERE status IN ('queued','running')")
                db.commit()
            if destination.exists():
                raise ReviewError("restore_target_exists", "Restore into a new, empty directory. Existing data is never overwritten.")
            # Windows directory rename is atomic and refuses an existing target.
            # No second copy (or duplicate disk allocation) is needed.
            temp.rename(destination)
    return destination
