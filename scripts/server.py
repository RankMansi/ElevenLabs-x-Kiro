#!/usr/bin/env python3
"""
Echoes — Web UI Server
Exposes HTTP endpoints for the web UI to trigger echoes.py
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

from dotenv import load_dotenv

load_dotenv()

SCRIPT_DIR = Path(__file__).parent
ECHOES_SCRIPT = SCRIPT_DIR / "echoes.py"
PYTHON = sys.executable


class EchoesHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f"  [{self.address_string()}] {format % args}")

    def send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            index = SCRIPT_DIR.parent / "index.html"
            if index.exists():
                body = index.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_json({"error": "index.html not found"}, 404)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/api/generate":
            # Optional --dir from request body
            repo_dir = body.get("dir")
            args = [str(ECHOES_SCRIPT), "--json"]
            if repo_dir:
                args += ["--dir", repo_dir]
            self._run_echoes(args)

        elif self.path == "/api/generate-repo":
            # Optional --dir from request body
            repo_dir = body.get("dir")
            args = [str(ECHOES_SCRIPT), "--json"]
            if repo_dir:
                args += ["--dir", repo_dir]
            self._run_echoes(args)

        else:
            self.send_json({"error": "not found"}, 404)

    def _run_echoes(self, args: list):
        try:
            result = subprocess.run(
                [PYTHON] + args,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=os.getcwd()
            )
            if result.returncode == 0:
                # echoes.py --json prints a single JSON line to stdout
                output = result.stdout.strip()
                try:
                    data = json.loads(output.split("\n")[-1])
                except Exception:
                    data = {"output": output}
                self.send_json({"status": "ok", **data})
            else:
                self.send_json({
                    "status": "error",
                    "output": result.stdout,
                    "error": result.stderr
                }, 500)
        except subprocess.TimeoutExpired:
            self.send_json({"error": "timed out after 300s"}, 504)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8765))
    server = HTTPServer(("0.0.0.0", port), EchoesHandler)
    print(f"\n🎭 Echoes server running → http://localhost:{port}")
    print("   POST /api/generate       — analyze repo (body: {\"dir\": \"/optional/path\"})")
    print("   POST /api/generate-repo  — same endpoint, alias\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
