"""Bound bytes before multipart parsing can spool an oversized upload to disk."""
from starlette.datastructures import Headers
from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse


class ReviewBodyLimit:
    def __init__(self, app, max_upload_bytes):
        self.app = app
        self.upload_limit = (max_upload_bytes or 500 * 1024**2) + 64 * 1024

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/v2/"):
            return await self.app(scope, receive, send)
        is_upload = scope["path"] == "/api/v2/documents" and scope["method"] == "POST"
        limit = self.upload_limit if is_upload else 2 * 1024**2
        headers = Headers(scope=scope)
        try:
            length = int(headers.get("content-length", "0"))
            if length < 0:
                raise ValueError()
        except ValueError:
            return await JSONResponse({"detail": "Invalid request length."}, status_code=400)(scope, receive, send)
        message = "Request exceeds the local upload or editing limit."
        if length > limit:
            return await JSONResponse({"detail": message, "code": "upload_limit"}, status_code=413)(scope, receive, send)
        received, exceeded = 0, False
        async def bounded_receive():
            nonlocal received, exceeded
            event = await receive()
            received += len(event.get("body", b""))
            if received > limit:
                exceeded = True
                # Starlette's multipart parser catches this and closes its temporary files.
                raise MultiPartException(message)
            return event
        async def bounded_send(event):
            if exceeded and event["type"] == "http.response.start":
                event = {**event, "status": 413}
            await send(event)
        await self.app(scope, bounded_receive, bounded_send)
