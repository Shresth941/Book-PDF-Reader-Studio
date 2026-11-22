"""API export publication failures; no PDF parser, heavy worker or real documents."""
from pathlib import Path
import sqlite3
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.review_routes import ReviewAPI
from app.core.config import Settings


@pytest.fixture
def export_api(tmp_path, monkeypatch):
    service = ReviewAPI(Settings(data_dir=tmp_path / "workspace"))
    monkeypatch.setattr(service.runner, "start", lambda: None)
    monkeypatch.setattr(service.runner, "close", lambda: None)
    identifier = str(uuid4())
    original = service.store.root / "originals" / f"{identifier}.pdf"
    original.write_bytes(b"Original publication test fixture")
    service.store.create_document(identifier, "fixture.pdf", "fixture", {
        "status": "ready", "page_count": 1,
        "pages": [{"number": 1, "width": 300, "height": 200}],
    })
    sentinel = service.store.root / "artifacts" / "existing-review.txt"
    sentinel.write_bytes(b"Existing unrelated export")
    app = FastAPI()
    service.install(app)
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
        yield service, client, identifier, sentinel, original


def request_export(service, client, identifier):
    return client.post(f"/api/v2/documents/{identifier}/exports",
                       json={"revision": 1, "format": "txt"},
                       headers={"X-Review-Token": service.token})


@pytest.mark.parametrize("failure", ["hash", "insert", "event", "commit"])
def test_unregistered_completed_export_is_removed_on_publication_failure(export_api, monkeypatch, failure):
    service, client, identifier, sentinel, original = export_api
    calls = []
    real_connect = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        inserted_export = False

        def execute(self, sql, parameters=()):
            if sql.startswith("INSERT INTO exports"):
                if failure == "insert":
                    calls.append("insert")
                    raise sqlite3.OperationalError("Injected registration write failure")
                self.inserted_export = True
            return super().execute(sql, parameters)

        def commit(self):
            if self.inserted_export and failure == "commit":
                calls.append("commit")
                raise sqlite3.OperationalError("Injected export transaction commit failure")
            return super().commit()

    def failing_connect(*args, **kwargs):
        return real_connect(*args, **kwargs, factory=FailingConnection)

    def fail_hash(*args, **kwargs):
        calls.append("hash")
        raise OSError("Injected artifact read failure")

    def fail_event(*args, **kwargs):
        calls.append("event")
        raise sqlite3.OperationalError("Injected event write failure")

    def worker(kind, payload):
        assert kind == "export"
        path = Path(payload["output_path"])
        path.write_bytes(b"Completed output waiting for registration")
        path.with_suffix(".combined.pdf").write_bytes(b"Request-owned companion temporary output")
        # Install only after the snapshot transaction; failures target publication.
        if failure == "hash":
            monkeypatch.setattr("app.api.review_routes.hashlib.file_digest", fail_hash)
        elif failure == "event":
            monkeypatch.setattr(service.store, "event", fail_event)
        else:
            monkeypatch.setattr(sqlite3, "connect", failing_connect)
        return {"path": str(path), "page_count": 0, "text_verified": True}

    monkeypatch.setattr(service.worker, "run", worker)
    response = request_export(service, client, identifier)
    assert response.status_code == 500
    assert calls == [failure], "The intended publication boundary must actually fail"
    assert list((service.store.root / "artifacts").iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"Existing unrelated export"
    assert original.read_bytes() == b"Original publication test fixture"
    with service.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM exports").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM events WHERE action='export'").fetchone()[0] == 0


@pytest.mark.parametrize("response_failure", [False, True])
def test_committed_export_survives_success_or_later_response_failure(export_api, monkeypatch, response_failure):
    service, client, identifier, sentinel, original = export_api
    public_export = service.public_export

    def worker(kind, payload):
        path = Path(payload["output_path"])
        path.write_bytes(b"Committed review copy")
        return {"path": str(path), "page_count": 0, "text_verified": True}

    def fail_response(*args):
        raise RuntimeError("Injected response formatting failure after commit")

    monkeypatch.setattr(service.worker, "run", worker)
    if response_failure:
        monkeypatch.setattr(service, "public_export", fail_response)
    response = request_export(service, client, identifier)
    assert response.status_code == (500 if response_failure else 201)
    with service.store.connection() as db:
        rows = db.execute("SELECT id FROM exports").fetchall()
        assert len(rows) == 1
        assert db.execute("SELECT COUNT(*) FROM events WHERE action='export'").fetchone()[0] == 1
    monkeypatch.setattr(service, "public_export", public_export)
    registered = service.export_record(rows[0][0])
    assert Path(registered["body"]["path"]).read_bytes() == b"Committed review copy"
    assert client.get(f"/api/v2/exports/{rows[0][0]}/download").content == b"Committed review copy"
    assert sentinel.read_bytes() == b"Existing unrelated export"
    assert original.read_bytes() == b"Original publication test fixture"
