from fastapi import FastAPI, File, UploadFile
from fastapi.testclient import TestClient
from app.api.body_limit import ReviewBodyLimit


def client():
    app = FastAPI()
    app.add_middleware(ReviewBodyLimit, max_upload_bytes=1024)
    @app.post("/api/v2/documents")
    async def upload(file: UploadFile = File(...)):
        return {"length": len(await file.read())}
    return TestClient(app, base_url="http://127.0.0.1")


def test_declared_oversized_body_rejected_before_file_handler():
    with client() as api:
        response = api.post("/api/v2/documents", files={"file": ("huge.pdf", b"x" * 70000, "application/pdf")})
        assert response.status_code == 413
        assert response.json()["code"] == "upload_limit"


def test_chunked_multipart_body_has_same_limit():
    chunks = [b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="large.pdf"\r\nContent-Type: application/pdf\r\n\r\n',
              b"x" * 40000, b"x" * 40000, b"\r\n--boundary--\r\n"]
    with client() as api:
        response = api.post("/api/v2/documents", content=iter(chunks), headers={"Content-Type": "multipart/form-data; boundary=boundary"})
        assert response.status_code == 413
        assert "limit" in response.json()["detail"]


def test_small_multipart_still_reaches_file_handler():
    with client() as api:
        response = api.post("/api/v2/documents", files={"file": ("small.pdf", b"x" * 100, "application/pdf")})
        assert response.status_code == 200
        assert response.json() == {"length": 100}
