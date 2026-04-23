#!/usr/bin/env python3
"""
Echoes — Web UI Server
Serves index.html and runs all generation jobs asynchronously.

Endpoints:
  GET  /                          → index.html
  GET  /audio/<filename>          → serve generated mp3
  GET  /video/<filename>          → serve generated mp4
  POST /api/podcast/start         → {github_url} → {job_id}
  GET  /api/podcast/status/<id>   → {status, result}
  POST /api/echoes/start          → {dir} → {job_id}
  GET  /api/echoes/status/<id>    → {status, result}
  POST /api/video/start           → {dir} → {job_id}
  GET  /api/video/status/<id>     → {status, result}
"""

import os, sys, json, subprocess, uuid, threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

load_dotenv()

SCRIPT_DIR     = Path(__file__).parent
PODCAST_SCRIPT = SCRIPT_DIR / "podcast.py"
ECHOES_SCRIPT  = SCRIPT_DIR / "echoes.py"
VIDEO_SCRIPT   = SCRIPT_DIR / "video.py"
PYTHON         = sys.executable

# Pass env vars (including PUTER_AUTH_TOKEN) to child processes
CHILD_ENV = {**os.environ}

# In-memory job store
JOBS = {}


# ── Job runners ───────────────────────────────────────────────────────────────

def _parse_output(result, path_key: str, url_prefix: str) -> dict:
    """Extract JSON from subprocess stdout and build result dict."""
    output = result.stdout.strip()
    for line in reversed(output.split("\n")):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                if data.get("success") or data.get("audio_path") or data.get("video_path"):
                    file_path = data.get(path_key, "")
                    if file_path:
                        data[f"_{path_key}"] = file_path  # store for serving
                        data[path_key.replace("_path", "_url")] = f"{url_prefix}{Path(file_path).name}"
                    return {"status": "done", "result": data}
                elif data.get("error"):
                    return {"status": "error", "error": data["error"]}
            except Exception:
                continue
    stderr = result.stderr[-600:] if result.stderr else "No output"
    return {"status": "error", "error": stderr}


def run_podcast_job(job_id: str, github_url: str):
    try:
        result = subprocess.run(
            [PYTHON, str(PODCAST_SCRIPT), "--github", github_url, "--json"],
            capture_output=True, text=True, timeout=600,
            cwd=str(SCRIPT_DIR.parent), env=CHILD_ENV
        )
        JOBS[job_id] = _parse_output(result, "audio_path", "/audio/")
    except subprocess.TimeoutExpired:
        JOBS[job_id] = {"status": "error", "error": "Timed out after 600s"}
    except Exception as e:
        JOBS[job_id] = {"status": "error", "error": str(e)}


def run_echoes_job(job_id: str, repo_dir: str):
    try:
        result = subprocess.run(
            [PYTHON, str(ECHOES_SCRIPT), "--dir", repo_dir, "--json"],
            capture_output=True, text=True, timeout=300,
            cwd=str(SCRIPT_DIR.parent), env=CHILD_ENV
        )
        JOBS[job_id] = _parse_output(result, "audio_path", "/audio/")
    except subprocess.TimeoutExpired:
        JOBS[job_id] = {"status": "error", "error": "Timed out after 300s"}
    except Exception as e:
        JOBS[job_id] = {"status": "error", "error": str(e)}


def run_video_job(job_id: str, repo_dir: str):
    try:
        result = subprocess.run(
            [PYTHON, str(VIDEO_SCRIPT), "--dir", repo_dir, "--json"],
            capture_output=True, text=True, timeout=900,
            cwd=str(SCRIPT_DIR.parent), env=CHILD_ENV
        )
        JOBS[job_id] = _parse_output(result, "video_path", "/video/")
    except subprocess.TimeoutExpired:
        JOBS[job_id] = {"status": "error", "error": "Timed out after 900s"}
    except Exception as e:
        JOBS[job_id] = {"status": "error", "error": str(e)}


def start_job(target_fn, *args) -> str:
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {"status": "running"}
    threading.Thread(target=target_fn, args=(job_id, *args), daemon=True).start()
    return job_id


# ── File serving helper ───────────────────────────────────────────────────────

def serve_file(path_key: str, filename: str, content_type: str, handler):
    # Check job store first (fastest)
    for job in JOBS.values():
        if job.get("status") == "done":
            stored = job["result"].get(f"_{path_key}", "")
            if stored and Path(stored).name == filename and Path(stored).exists():
                body = Path(stored).read_bytes()
                handler.send_response(200)
                handler.send_header("Content-Type", content_type)
                handler.send_header("Content-Length", len(body))
                handler.send_header("Accept-Ranges", "bytes")
                handler.end_headers()
                handler.wfile.write(body)
                return True

    # Fallback: search /tmp
    for search_dir in ["/tmp", "/var/folders"]:
        try:
            for p in Path(search_dir).rglob(filename):
                body = p.read_bytes()
                handler.send_response(200)
                handler.send_header("Content-Type", content_type)
                handler.send_header("Content-Length", len(body))
                handler.end_headers()
                handler.wfile.write(body)
                return True
        except Exception:
            continue
    return False


# ── HTTP Handler ──────────────────────────────────────────────────────────────

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
        p = self.path

        # index.html
        if p in ("/", "/index.html"):
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

        # Audio files
        elif p.startswith("/audio/"):
            filename = p[7:]
            if not serve_file("audio_path", filename, "audio/mpeg", self):
                self.send_json({"error": f"{filename} not found"}, 404)

        # Video files
        elif p.startswith("/video/"):
            filename = p[7:]
            if not serve_file("video_path", filename, "video/mp4", self):
                self.send_json({"error": f"{filename} not found"}, 404)

        # Job status
        elif p.startswith("/api/podcast/status/"):
            self.send_json(JOBS.get(p.split("/")[-1], {"status": "not_found"}))

        elif p.startswith("/api/echoes/status/"):
            self.send_json(JOBS.get(p.split("/")[-1], {"status": "not_found"}))

        elif p.startswith("/api/video/status/"):
            self.send_json(JOBS.get(p.split("/")[-1], {"status": "not_found"}))

        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}
        p      = self.path

        if p == "/api/podcast/start":
            github_url = body.get("github_url", "").strip()
            if not github_url:
                self.send_json({"detail": "github_url required"}, 400); return
            self.send_json({"job_id": start_job(run_podcast_job, github_url)})

        elif p == "/api/echoes/start":
            repo_dir = body.get("dir", "").strip()
            if not repo_dir:
                self.send_json({"detail": "dir required"}, 400); return
            self.send_json({"job_id": start_job(run_echoes_job, repo_dir)})

        elif p == "/api/video/start":
            repo_dir = body.get("dir", "").strip()
            if not repo_dir:
                self.send_json({"detail": "dir required"}, 400); return
            self.send_json({"job_id": start_job(run_video_job, repo_dir)})

        else:
            self.send_json({"error": "not found"}, 404)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8765))
    server = HTTPServer(("0.0.0.0", port), EchoesHandler)
    print(f"\n🎙️  Echoes server → http://localhost:{port}")
    print(f"   Podcast:  POST /api/podcast/start  {{github_url}}")
    print(f"   Drama:    POST /api/echoes/start   {{dir}}")
    print(f"   Film:     POST /api/video/start    {{dir}}")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
