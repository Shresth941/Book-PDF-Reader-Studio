import fitz
import httpx
from fastapi.testclient import TestClient
from app.main import app, documents

def test_full_pipeline_with_test_client(tmp_path):
    # Keep integration uploads out of the running application's persistent
    # document folder. This avoids tests competing with user files or a local
    # server that happens to hold that folder open.
    original_root = documents.storage.root
    documents.storage.root = tmp_path
    try:
        with TestClient(app) as client:
            # 1. Health
            health = client.get("/api/health").json()
            assert health == {"status": "ok"}

            # 2. Upload
            pdf = fitz.open()
            for i in range(1, 4):
                p = pdf.new_page()
                p.insert_text((72, 72), f"Page {i} content: document extraction and translation.")
            pdf_bytes = pdf.tobytes()

            files = {"file": ("sample.pdf", pdf_bytes, "application/pdf")}
            res = client.post("/api/documents", files=files)
            assert res.status_code == 201
            doc_data = res.json()
            doc_id = doc_data["id"]

            # 3. Read page range 1-2
            pages_res = client.get(f"/api/documents/{doc_id}/pages?start=1&end=2")
            assert pages_res.status_code == 200
            pages_data = pages_res.json()
            assert len(pages_data["pages"]) == 2

            # 4. Definition with punctuation
            def_res = client.post("/api/definitions", json={"term": "document.", "language": "en"})
            assert def_res.status_code == 200
            assert isinstance(def_res.json().get("definitions"), list)
    finally:
        documents.storage.root = original_root
