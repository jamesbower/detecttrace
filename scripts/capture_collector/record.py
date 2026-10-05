"""Record every HTTP request it receives, then answer like an OTLP/HTTP endpoint.

Standard library only. Each request becomes two files in the output directory:
NNN_<label>.body (the raw bytes exactly as received) and NNN_<label>.json (method,
path, and the headers). The label is the first path segment, so /json/v1/traces
and /proto/v1/traces say which exporter sent the request.

Usage: python record.py OUTPUT_DIR [PORT]
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

SECRET_HEADERS = {"authorization", "proxy-authorization", "cookie", "x-api-key"}
DEFAULT_PORT = 18080


class Recorder(BaseHTTPRequestHandler):
    output_dir: Path
    lock = Lock()
    counter = 0

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self._save(body)
        reply = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def _save(self, body: bytes) -> None:
        label = self.path.strip("/").split("/")[0] or "root"
        with Recorder.lock:
            Recorder.counter += 1
            stem = f"{Recorder.counter:03d}_{label}"
        headers = {}
        for name, value in self.headers.items():
            is_secret = name.lower() in SECRET_HEADERS
            headers[name] = value.split(" ")[0] + " [redacted]" if is_secret else value
        meta = {"method": self.command, "path": self.path, "headers": headers}
        text = json.dumps(meta, indent=2, sort_keys=True) + "\n"
        (self.output_dir / f"{stem}.json").write_text(text, encoding="utf-8")
        (self.output_dir / f"{stem}.body").write_bytes(body)
        print(f"{stem}: {self.path} {len(body)} bytes", file=sys.stderr, flush=True)

    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    Recorder.output_dir = Path(sys.argv[1])
    Recorder.output_dir.mkdir(parents=True, exist_ok=True)
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    ThreadingHTTPServer(("0.0.0.0", port), Recorder).serve_forever()


if __name__ == "__main__":
    main()
