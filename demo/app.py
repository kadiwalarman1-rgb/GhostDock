"""Small stateless HTTP service with an identity-bearing health endpoint."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in {"/", "/healthz"}:
            self.send_error(404)
            return
        # Public test flag inside isolated container tmpfs, not a secret or temporary file creation.
        unhealthy = os.path.exists("/tmp/ghostdock-unhealthy")  # nosec B108
        status = 503 if unhealthy else 200
        body = json.dumps(
            {"service": "ghostdock-demo", "status": "unhealthy" if unhealthy else "ok", "version": "0.1.0"}
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
