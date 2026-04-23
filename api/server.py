"""
Echoes — FastAPI entrypoint for Vercel deployment.
Mirrors all routes from scripts/server.py.
"""
import os, sys, json, subprocess, uuid, threading
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# Resolve paths relative to repo root
ROOT       = Path(__file__).parent.parent
SCRIPT_DIR = ROOT / "scripts"
PYTHON     = sys.executable
CHILD_ENV  = {**os.environ}
JOBS: dict = {}

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Models ────────────────────────────────────────────────────────────────────

class PodcastRequest(BaseModel):
    github_url: str

class DirRequest(BaseModel):
    dir: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_output(result, path_key: str, url_prefix: str) -> dict:
    for line in reversed(result.stdout.strip().split("\n")):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                if data.get("success") or data.get(path_key):
                    file_path = data.get(path_key, "")
                    if file_path:
                        data[f"_{path_key}"] = file_path
                        data[path_key.replace("_path", "_url")] = f"{url_prefix}{Path(file_path).name}"
                    return {"status": "done", "result": data}
                elif data.get("error"):
                    return {"status": "error", "error": data["error"]}
            except Exception:
                continue
    return {"status": "error", "error": result.stderr[-600:] or "No output"}


def _start_job(fn, *args) -> str:
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {"status": "running"}
    threading.Thread(target=fn, args=(job_id, *args), daemon=True).start()
    return job_id


def _find_file(filename: str) -> Path | None:
    for job in JOBS.values():
        if job.get("status") == "done":
            p = job["result"].get(f"_audio_path") or job["result"].get(f"_video_path", "")
            if p and Path(p).name == filename and Path(p).exists():
                return Path(p)
    for d in ["/tmp", "/var/folders"]:
        for p in Path(d).rglob(filename) if Path(d).exists() else []:
            return p
    return None


# ── Job runners ───────────────────────────────────────────────────────────────

def _run_podcast(job_id: str, github_url: str):
    try:
        r = subprocess.run(
            [PYTHON, str(SCRIPT_DIR / "podcast.py"), "--github", github_url, "--json"],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT), env=CHILD_ENV)
        JOBS[job_id] = _parse_output(r, "audio_path", "/audio/")
    except subprocess.TimeoutExpired:
        JOBS[job_id] = {"status": "error", "error": "Timed out after 600s"}
    except Exception as e:
        JOBS[job_id] = {"status": "error", "error": str(e)}


def _run_echoes(job_id: str, repo_dir: str):
    try:
        r = subprocess.run(
            [PYTHON, str(SCRIPT_DIR / "echoes.py"), "--dir", repo_dir, "--json"],
            capture_output=True, text=True, timeout=300, cwd=str(ROOT), env=CHILD_ENV)
        JOBS[job_id] = _parse_output(r, "audio_path", "/audio/")
    except subprocess.TimeoutExpired:
        JOBS[job_id] = {"status": "error", "error": "Timed out after 300s"}
    except Exception as e:
        JOBS[job_id] = {"status": "error", "error": str(e)}


def _run_video(job_id: str, repo_dir: str):
    try:
        r = subprocess.run(
            [PYTHON, str(SCRIPT_DIR / "video.py"), "--dir", repo_dir, "--json"],
            capture_output=True, text=True, timeout=900, cwd=str(ROOT), env=CHILD_ENV)
        JOBS[job_id] = _parse_output(r, "video_path", "/video/")
    except subprocess.TimeoutExpired:
        JOBS[job_id] = {"status": "error", "error": "Timed out after 900s"}
    except Exception as e:
        JOBS[job_id] = {"status": "error", "error": str(e)}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    f = ROOT / "index.html"
    if not f.exists():
        raise HTTPException(404, "index.html not found")
    return HTMLResponse(f.read_text())


@app.get("/audio/{filename}")
def serve_audio(filename: str):
    p = _find_file(filename)
    if not p:
        raise HTTPException(404, f"{filename} not found")
    return FileResponse(str(p), media_type="audio/mpeg")


@app.get("/video/{filename}")
def serve_video(filename: str):
    p = _find_file(filename)
    if not p:
        raise HTTPException(404, f"{filename} not found")
    return FileResponse(str(p), media_type="video/mp4")


@app.post("/api/podcast/start")
def podcast_start(req: PodcastRequest):
    if not req.github_url:
        raise HTTPException(400, "github_url required")
    return {"job_id": _start_job(_run_podcast, req.github_url)}


@app.get("/api/podcast/status/{job_id}")
def podcast_status(job_id: str):
    return JOBS.get(job_id, {"status": "not_found"})


@app.post("/api/echoes/start")
def echoes_start(req: DirRequest):
    if not req.dir:
        raise HTTPException(400, "dir required")
    return {"job_id": _start_job(_run_echoes, req.dir)}


@app.get("/api/echoes/status/{job_id}")
def echoes_status(job_id: str):
    return JOBS.get(job_id, {"status": "not_found"})


@app.post("/api/video/start")
def video_start(req: DirRequest):
    if not req.dir:
        raise HTTPException(400, "dir required")
    return {"job_id": _start_job(_run_video, req.dir)}


@app.get("/api/video/status/{job_id}")
def video_status(job_id: str):
    return JOBS.get(job_id, {"status": "not_found"})
