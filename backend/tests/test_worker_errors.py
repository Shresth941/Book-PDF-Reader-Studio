"""Resource failures must offer recovery without exposing document diagnostics."""
import pytest

from app.core.review import ReviewError
from app.infrastructure.worker import error_result


@pytest.mark.parametrize("error", [MemoryError("private allocation detail"),
    RuntimeError("mkl_malloc: failed to allocate memory"), RuntimeError("std::bad_alloc")])
def test_allocation_failures_remain_actionable(error):
    result = error_result(error)
    assert result["error"] == "worker_memory" and result["status"] == 503
    assert "model loading" in result["message"]
    assert "private" not in result["message"]


def test_other_runtime_errors_do_not_expose_document_content():
    result = error_result(RuntimeError("Patient name and private document path"))
    assert result["error"] == "processing_failed"
    assert "Patient" not in result["message"] and "private" not in result["message"]


def test_explicit_review_error_keeps_its_recovery_message():
    assert error_result(ReviewError("model_missing", "Install the model.", 503)) == {
        "error": "model_missing", "message": "Install the model.", "status": 503}
