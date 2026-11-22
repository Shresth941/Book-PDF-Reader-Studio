"""A low-RAM pause must retain the PDF and recover without another upload."""
from types import SimpleNamespace

import fitz
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.review_routes import ReviewAPI
from app.core.config import Settings
from app.core.review import ReviewError
from app.infrastructure import bounded_worker
from app.infrastructure.structured_pdf import inspect_pdf


@pytest.mark.parametrize("available,allowed", [(1024 * 1024**2 - 1, False),
    (1024 * 1024**2, True), (1200 * 1024**2, True)])
def test_memory_status_uses_exact_reserve_boundary(monkeypatch, available, allowed):
    monkeypatch.setattr(bounded_worker.psutil, "virtual_memory", lambda:
                        SimpleNamespace(available=available, total=8192 * 1024**2))
    status = bounded_worker.memory_status(Settings())
    assert status["can_start_worker"] is allowed
    assert status["required_mb"] == 1024
    assert status["available_mb"] == available // 1024**2


def test_no_child_process_starts_below_reserve(monkeypatch):
    monkeypatch.setattr(bounded_worker.psutil, "virtual_memory", lambda:
                        SimpleNamespace(available=512 * 1024**2, total=8192 * 1024**2))
    monkeypatch.setattr(bounded_worker.subprocess, "Popen", lambda *a, **kw:
                        pytest.fail("A worker must not start below the memory reserve."))
    with pytest.raises(ReviewError) as error:
        bounded_worker.BoundedWorker(Settings()).run("inspect", {})
    assert error.value.code == "low_memory" and error.value.status == 503
    assert "512 MB" in error.value.message and "1024 MB" in error.value.message


def test_import_pause_and_retry_keep_same_document_and_original(tmp_path, monkeypatch):
    available = 512
    monkeypatch.setattr(bounded_worker.psutil, "virtual_memory", lambda:
                        SimpleNamespace(available=available * 1024**2, total=8192 * 1024**2))
    service = ReviewAPI(Settings(data_dir=tmp_path / "data", models_dir=tmp_path / "models"))
    app = FastAPI()
    service.install(app)
    with fitz.open() as pdf:
        pdf.new_page().insert_text((60, 60), "Preserve this original while RAM is low.")
        original = pdf.tobytes()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {"X-Review-Token": client.get("/api/v2/session").json()["token"]}
        response = client.post("/api/v2/documents", headers=headers,
            files={"file": ("memory.pdf", original, "application/pdf")})
        assert response.status_code == 201
        document = response.json()
        assert document["page_count"] == 0 and document["metadata"]["code"] == "low_memory"
        base = f"/api/v2/documents/{document['id']}"
        assert client.get(base + "/original").content == original
        assert client.get("/api/v2/resources").json()["memory"]["can_start_worker"] is False
        failed_retry = client.post(base + "/inspect", headers=headers, json={"password": ""})
        assert failed_retry.status_code == 503 and failed_retry.json()["code"] == "low_memory"
        assert client.get(base).json()["revision"] == document["revision"]
        available = 2048
        assert client.get("/api/v2/resources").json()["memory"]["can_start_worker"] is True
        # Parsing is already separately qualified in the actual isolated worker.
        # This injected tiny parser isolates API recovery from machine RAM load.
        monkeypatch.setattr(service.worker, "run", lambda operation, payload: inspect_pdf(**payload))
        restored = client.post(base + "/inspect", headers=headers, json={"password": ""})
        assert restored.status_code == 200
        assert restored.json()["id"] == document["id"] and restored.json()["page_count"] == 1
        assert restored.json()["status"] == "ready"
        assert client.get(base + "/original").content == original
