"""Serve only the generated browser fixture and its synthetic page image."""
import http.server
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2] / "tmp" / "workspace-race-qa"

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/v2/") and self.path.endswith("/image"):
            data = b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="400"><rect width="300" height="400" fill="white"/><text x="20" y="50">Synthetic source page</text></svg>'
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            super().do_GET()

if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", 8012), Handler).serve_forever()
