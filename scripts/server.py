#!/usr/bin/env python3
"""
Echoes — Web UI Server
Serves index.html and runs podcast.py jobs asynchronously.
"""

import os, sys, json, subprocess, uuid, threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

load_dotenv()

SCRIPT_DIR    = Path(__file__).parent
PODCAST_SCRIPT = SCRIPT_DIR / "podcast.py"
PYTHON        = sys.executable

# In-memory job store: {job_id: {"status": "running"|"done"|"error", "result": {...}, "error": str}}
JOBS = {}


def run_podcast_job(job_id: str, github_url: str):
    try:
        result = subprocess.run(
            [PYTHON, str(PODCAST_SCRIPT), "--github", github_url, "--json"],
            capture_output=True, text=True, timeout=600,
            cwd=str(SCRIPT_DIR.parent)
        )
        output = result.stdout.strip()
        # Last line should be the JSON payload
        for line in reversed(output.split("\n")):
            line = line.strip()
            if line.startswith("{"):
                data = json.loads(line)
                if data.get("success"):
                    # Expose audio as a URL path
                    audio_path = data.get("audio_path", "")
                    data["audio_url"] = f"/audio/{Path(audio_path).name}"
                    # Store file path for serving
                    data["_audio_path"] = audio_path
                    JOBS[job_id] = {"status": "done", "result": data}
                else:
                    JOBS[job_id] = {"status": "error", "error": data.get("error", "Unknown error")}
                return
        JOBS[job_id] = {"status": "error", "error": result.stderr[-500:] or "No JSON output"}
    except subprocess.TimeoutExpired:
        JOBS[job_id] = {"status": "error", "error": "Timed out after 600s"}
    except Exception as e:
        JOBS[job_id] = {"status": "error", "error": str(e)}


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
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            index = SCRIPT_DIR.parent / "index.html"
            if index.exists():
                body = index.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_json({"error": "index.html not found"}, 404)

        elif self.path.startswith("/audio/"):
            filename = self.path[7:]  # strip /audio/
            # Search common temp locations
            for search_dir in ["/tmp", "/var/folders"]:
                for p in Path(search_dir).rglob(filename):
                    body = p.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/mpeg")
                    self.send_header("Content-Length", len(body))
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()
                    self.wfile.write(body)
                    return
            # Also check job store for exact path
            for job in JOBS.values():
                if job.get("status") == "done":
                    ap = job["result"].get("_audio_path", "")
                    if Path(ap).name == filename and Path(ap).exists():
                        body = Path(ap).read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", "audio/mpeg")
                        self.send_header("Content-Length", len(body))
                        self.end_headers()
                        self.wfile.write(body)
                        return
            self.send_json({"error": "audio not found"}, 404)

        elif self.path.startswith("/api/podcast/status/"):
            job_id = self.path.split("/")[-1]
            job = JOBS.get(job_id)
            if not job:
                self.send_json({"status": "not_found"}, 404)
            else:
                self.send_json(job)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/api/podcast/start":
            github_url = body.get("github_url", "").strip()
            if not github_url:
                self.send_json({"detail": "github_url required"}, 400)
                return
            job_id = str(uuid.uuid4())[:8]
            JOBS[job_id] = {"status": "running"}
            t = threading.Thread(target=run_podcast_job, args=(job_id, github_url), daemon=True)
            t.start()
            self.send_json({"job_id": job_id})

        else:
            self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8765))
    server = HTTPServer(("0.0.0.0", port), EchoesHandler)
    print(f"\n🎙️  Echoes server → http://localhost:{port}")
    print(f"   Open that URL in your browser\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
