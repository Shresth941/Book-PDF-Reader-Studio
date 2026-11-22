"""Opt-in real-model API pilot; synthetic cases, never a quality benchmark."""
import json
import os
from pathlib import Path
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.review_routes import ReviewAPI
from app.core.config import Settings
from app.services.review_export import shaped_document, checked_pdf_output


pytestmark = pytest.mark.skipif(os.environ.get("RUN_LOCAL_MODEL_TESTS") != "1", reason="Explicit local-model integration pilot; requires installed public models.")


def wait_job(client, identifier, job_id):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        jobs = client.get(f"/api/v2/documents/{identifier}/jobs").json()["jobs"]
        job = next(j for j in jobs if j["id"] == job_id)
        if job["status"] == "completed":
            return job
        assert job["status"] not in {"paused", "cancelled"}, job["message"]
        time.sleep(0.1)
    pytest.fail("The synthetic model job did not finish within the pilot limit.")


@pytest.mark.parametrize("source,target", [("en", "hi"), ("hi", "en")])
def test_real_offline_model_through_import_review_jobs_and_final_gate(tmp_path, source, target):
    models = Path(__file__).resolve().parents[1] / "models"
    settings = Settings(data_dir=tmp_path / "workspace", models_dir=models,
                        worker_memory_mb=3072, minimum_free_memory_mb=128, model_timeout_seconds=120)
    service = ReviewAPI(settings)
    app = FastAPI()
    service.install(app)
    samples = (["This is a book.", "Do not sign unless the amount is correct.", "The debtor shall pay 100 rupees."]
               if source == "en" else ["यह एक पुस्तक है।", "राशि सही नहीं है।", "ऋणी को 100 रुपये देने होंगे।"])
    pdf = shaped_document(tmp_path)
    for sample in samples:
        pdf.add_page()
        pdf.multi_cell(0, 18, text=sample)
    original = bytes(checked_pdf_output(pdf))
    start = time.monotonic()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {"X-Review-Token": client.get("/api/v2/session").json()["token"]}
        caps = client.get("/api/v2/capabilities").json()["translation"][f"{source}-{target}"]
        assert caps["available"] and caps["provider"] == "marian-ct2" and caps["draft_only"]
        uploaded = client.post("/api/v2/documents", headers=headers, files={"file": ("synthetic-pilot.pdf", original, "application/pdf")})
        assert uploaded.status_code == 201, uploaded.text
        doc = uploaded.json()
        base = f"/api/v2/documents/{doc['id']}"
        response = client.patch(base + "/context", headers=headers, json={"revision": doc["revision"], "target": target, "domain": "general", "jurisdiction": ""})
        assert response.status_code == 200, response.text
        job = client.post(base + "/jobs", headers=headers, json={"kind": "extract", "start": 1, "end": 3}).json()
        wait_job(client, doc["id"], job["id"])
        for number, expected in enumerate(samples, 1):
            endpoint = base + f"/pages/{number}"
            page = client.get(endpoint).json()
            assert len(page["regions"]) == 1
            region = page["regions"][0]
            assert region["source"].strip() == expected
            patch = {k: region[k] for k in ("source", "translation", "disposition", "contains_text", "reason", "kind")}
            patch.update(source_language=source, revision=page["revision"])
            response = client.patch(endpoint + "/regions/" + region["id"], headers=headers, json=patch)
            assert response.status_code == 200, response.text
            response = client.post(endpoint + "/approvals", headers=headers, json={"revision": response.json()["revision"], "kind": "source", "region_id": region["id"]})
            assert response.status_code == 200, response.text
        job = client.post(base + "/jobs", headers=headers, json={"kind": "translate", "start": 1, "end": 3}).json()
        wait_job(client, doc["id"], job["id"])
        measured = []
        for number in range(1, 4):
            page = client.get(base + f"/pages/{number}").json()
            region = page["regions"][0]
            assert region["translation"].strip() and region["draft_only"]
            assert not region["translation_approved"] and not page["coverage_approved"]
            assert region["model"].startswith("Helsinki-NLP/")
            assert "".join(region["source"][a["source_start"]:a["source_end"]] for a in region["alignment"]) == region["source"]
            measured.append({"source": region["source"], "translation": region["translation"], "checks": region["checks"]})
        doc = client.get(base).json()
        exported = client.post(base + "/exports", headers=headers, json={"revision": doc["revision"], "format": "pdf", "mode": "readable"})
        assert exported.status_code == 201, exported.text
        artifact = exported.json()
        assert artifact["blockers"] and artifact["status"] == "draft_partial"
        assert client.get(f"/api/v2/exports/{artifact['id']}/download?final=true").status_code == 409
        assert client.get(base + "/original").content == original
    report = {"passed": True, "scope": "synthetic API/runtime pilot; not a bilingual quality assessment",
              "direction": f"{source}-{target}", "pages": 3, "elapsed_seconds": round(time.monotonic() - start, 3),
              "worker_memory_limit_mb": 3072, "test_free_memory_reserve_mb": 128, "samples": measured}
    (tmp_path / "real-model-api-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
