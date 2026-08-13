"""Serve the mock chat page at http://localhost:5599/chat (no deps, stdlib only)."""

from __future__ import annotations

import http.server
from pathlib import Path

PAGE = (Path(__file__).parent / "chat.html").read_bytes()
PORT = 5599


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") in ("", "/chat"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGE)
        else:
            self.send_error(404)

    def log_message(self, *_):  # keep test output quiet
        pass


if __name__ == "__main__":
    print(f"Mock SUT chat on http://localhost:{PORT}/chat")
    http.server.ThreadingHTTPServer(("", PORT), Handler).serve_forever()
