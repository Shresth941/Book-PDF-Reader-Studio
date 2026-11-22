"""Executable isolated worker. Do not import PDF/ML runtimes before stdin handshake."""
import json
import sys


def error_result(exc):
    """Expose actionable allocation failures without leaking parser/model text."""
    allocation_failed = isinstance(exc, MemoryError) or (
        isinstance(exc, RuntimeError) and str(exc).startswith((
            "mkl_malloc: failed to allocate memory", "std::bad_alloc")))
    if allocation_failed:
        return {"error": "worker_memory", "message":
                "This step could not allocate memory within its worker budget. "
                "Close other applications and resume. A smaller passage may help during processing; "
                "model loading still requires enough memory for the full model.", "status": 503}
    return {"error": getattr(exc, "code", "processing_failed"),
            "message": getattr(exc, "message", "This processing step failed. Preserve the original and review or retry this unit."),
            "status": getattr(exc, "status", 422)}


def main():
    request = json.loads(sys.stdin.buffer.read())
    result_stream = sys.stdout
    sys.stdout = sys.stderr
    import os
    if os.name != "nt":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (request["memory_bytes"], request["memory_bytes"]))
    try:
        from app.core.review import ReviewError
        operation, payload = request["operation"], request["payload"]
        if operation == "inspect":
            from app.infrastructure.structured_pdf import inspect_pdf
            result = inspect_pdf(**payload)
        elif operation == "extract":
            from app.infrastructure.structured_pdf import extract_page
            result = extract_page(**payload)
        elif operation == "render":
            from app.infrastructure.structured_pdf import render_page
            result = render_page(**payload)
        elif operation == "inspect_forms":
            from app.infrastructure.structured_pdf import inspect_page_forms
            result = inspect_page_forms(**payload)
        elif operation == "verify_original":
            from app.infrastructure.structured_pdf import verify_original
            result = verify_original(**payload)
        elif operation == "translate":
            from app.services.local_translation import translate_segments
            result = translate_segments(**payload)
        elif operation == "export":
            from app.services.review_export import build_export
            result = build_export(**payload)
        else:
            raise ReviewError("unknown_operation", "Unknown worker operation.")
    except Exception as exc:
        # Parser/model messages can include document text/paths: do not expose them.
        result = error_result(exc)
    result_stream.write(json.dumps(result, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    main()
