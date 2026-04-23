#!/usr/bin/env python3
"""
Echoes Podcast — Repo Podcast Generator
Turns any GitHub repo into a full podcast episode with host voice,
interview segments, fake ad breaks, music stings, and chapter markers.

Run: python scripts/podcast.py --github https://github.com/elevenlabs/skills
Run: python scripts/podcast.py --dir /path/to/local/repo
Run (web):  python scripts/podcast.py --github <url> --json
"""

import sys, os, json, subprocess, hashlib, tempfile, shutil, atexit, re, time
from pathlib import Path
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY")

if not ELEVENLABS_API_KEY:
    print(json.dumps({"error": "ELEVENLABS_API_KEY not set"})); sys.exit(1)
if not GEMINI_API_KEY:
    print(json.dumps({"error": "GEMINI_API_KEY not set"})); sys.exit(1)

client       = ElevenLabs(api_key=ELEVENLABS_API_KEY)
VOICE_CACHE  = Path(".podcast_voices")
CLEANUP_DIRS = []

# Fallback library voices
FALLBACK_HOST  = "JBFqnCBsd6RMkjVDRZzb"  # George
FALLBACK_GUEST = "EXAVITQu4vr4xnSDxMaL"  # Bella
FALLBACK_AD    = "pqHfZKP75CvOlQylNhV4"  # Bill


def _cleanup():
    for d in CLEANUP_DIRS:
        shutil.rmtree(d, ignore_errors=True)

atexit.register(_cleanup)


def log(msg, json_mode=False):
    if not json_mode:
        print(msg)


# ── Step 1: Repo fetching ──────────────────────────────────────────────────────

def clone_github_repo(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.endswith(".git"):
        url += ".git"
    repo_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", url.split("github.com/")[-1].replace(".git", ""))
    tmpdir = tempfile.mkdtemp(prefix=f"echoes_pod_{repo_slug}_")
    CLEANUP_DIRS.append(tmpdir)
    result = subprocess.run(
        ["git", "clone", "--depth=150", "--quiet", url, tmpdir],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"Clone failed: {result.stderr[:300]}")
    return tmpdir


def read_repo_data(repo_dir: str) -> dict:
    cwd = os.path.abspath(repo_dir)

    # README
    readme = ""
    for name in ["README.md", "README.rst", "README.txt", "README"]:
        p = Path(cwd) / name
        if p.exists():
            readme = p.read_text(errors="replace")[:3000]
            break

    # Git log
    r = subprocess.run(
        ["git", "log", "--format=%an|||%ae|||%ad|||%s", "--date=short"],
        capture_output=True, text=True, cwd=cwd, timeout=20
    )
    commits = []
    for line in r.stdout.strip().split("\n"):
        if "|||" not in line:
            continue
        p = line.split("|||")
        if len(p) >= 4:
            commits.append({"author": p[0].strip(), "email": p[1].strip(),
                            "date": p[2].strip(), "subject": p[3].strip()})

    # Author stats
    author_counts = {}
    for c in commits:
        author_counts[c["author"]] = author_counts.get(c["author"], 0) + 1
    top_authors = sorted(author_counts.items(), key=lambda x: x[1], reverse=True)[:6]

    # Most changed files
    fc = subprocess.run(["git", "log", "--name-only", "--format="],
                        capture_output=True, text=True, cwd=cwd, timeout=15)
    file_counts = {}
    for f in fc.stdout.strip().split("\n"):
        f = f.strip()
        if f and not f.startswith("."):
            file_counts[f] = file_counts.get(f, 0) + 1
    hot_files = sorted(file_counts.items(), key=lambda x: x[1], reverse=True)[:8]

    # Tracked files
    ls = subprocess.run(
        ["git", "ls-files", "--", "*.py", "*.js", "*.ts", "*.go", "*.rs", "*.rb", "*.md"],
        capture_output=True, text=True, cwd=cwd, timeout=10
    )
    tracked_files = [f for f in ls.stdout.strip().split("\n") if f][:30]

    # Date range
    try:
        first_date = subprocess.run(
            ["git", "log", "--reverse", "--format=%ad", "--date=short", "--"],
            capture_output=True, text=True, cwd=cwd
        ).stdout.strip().split("\n")[0]
        last_date = commits[0]["date"] if commits else "recently"
    except Exception:
        first_date, last_date = "unknown", "recently"

    # Primary language
    ext_counts = {}
    for f in tracked_files:
        ext = Path(f).suffix.lower()
        if ext:
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
    primary_lang = max(ext_counts, key=ext_counts.get, default=".py") if ext_counts else ".py"
    lang_map = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
                ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".java": "Java",
                ".cs": "C#", ".cpp": "C++"}
    # Exclude .md from language detection — it's docs, not code
    code_ext_counts = {k: v for k, v in ext_counts.items() if k != ".md"}
    best_ext = max(code_ext_counts, key=code_ext_counts.get, default=".py") if code_ext_counts else ".py"
    language = lang_map.get(best_ext, best_ext.lstrip(".").capitalize())

    # Dramatic commits
    drama_keywords = ["fix", "revert", "hotfix", "rollback", "critical", "urgent",
                      "broken", "oops", "mistake", "wrong", "bad", "sorry", "hack", "temp", "todo"]
    dramatic_commits = [c for c in commits if any(k in c["subject"].lower() for k in drama_keywords)][:5]

    repo_name = Path(cwd).name.replace("-", " ").replace("_", " ").title()

    return {
        "repo_name": repo_name, "repo_dir": repo_dir, "readme": readme,
        "total_commits": len(commits), "top_authors": top_authors,
        "hot_files": hot_files, "tracked_files": tracked_files,
        "first_date": first_date, "last_date": last_date, "language": language,
        "dramatic_commits": dramatic_commits, "recent_commits": commits[:8],
        "all_commits": commits[:40],
    }


# ── Step 2: Podcast script via Gemini ─────────────────────────────────────────

def generate_podcast_script(data: dict, json_mode=False) -> dict:
    import openai as _openai

    puter_token = os.getenv("PUTER_AUTH_TOKEN")
    oc = _openai.OpenAI(
        base_url="https://api.puter.com/puterai/openai/v1/",
        api_key=puter_token,
    )
    repo     = data["repo_name"]
    authors  = ", ".join([f"{a} ({n} commits)" for a, n in data["top_authors"][:4]])
    files    = ", ".join([f[0] for f in data["hot_files"][:5]])
    total    = data["total_commits"]
    language = data["language"]
    first    = data["first_date"]
    readme   = data["readme"][:1500] if data["readme"] else "No README found."
    recent   = "\n".join([f"- [{c['date']}] {c['author']}: {c['subject']}" for c in data["recent_commits"][:6]])
    dramatic = "\n".join([f"- {c['subject']} ({c['author']}, {c['date']})" for c in data["dramatic_commits"][:3]])

    top_author   = data["top_authors"][0][0] if data["top_authors"] else "the main developer"
    guest_author = data["top_authors"][1][0] if len(data["top_authors"]) > 1 else top_author

    ad_map = {
        "Python":     ("Semicolons Pro", "the semicolon manager for Python developers — because sometimes you miss them"),
        "JavaScript": ("npm Detox", "the dependency rehab program — 847 packages is too many, and you know it"),
        "TypeScript": ("TypeScript Therapy", "for developers still arguing about 'any' with their teammates"),
        "Go":         ("Go Error Handler", "if err != nil, this ad would be twice as long"),
        "Rust":       ("Borrow Checker Anonymous", "a support group for developers whose code still won't compile"),
        "Ruby":       ("Rails Magic Explainer", "because someone on your team doesn't know where that method comes from"),
    }
    ad_product, ad_pitch = ad_map.get(language, ("Stack Overflow Premium", "the site that wrote half your codebase, according to git blame"))

    dramatic_snippet = dramatic[:100] if dramatic else "a difficult period"
    first_hot_file   = files.split(",")[0].strip() if files else "that file"

    # Build story arc from commit history
    early_commits  = [c for c in data["all_commits"] if any(k in c["subject"].lower() for k in ["init","add","create","start","first"])][:2]
    messy_commits  = [c for c in data["all_commits"] if any(k in c["subject"].lower() for k in ["fix","hack","temp","wip","quick","patch"])][:3]
    regret_commits = [c for c in data["all_commits"] if any(k in c["subject"].lower() for k in ["revert","broken","oops","wrong","sorry","remove"])][:2]

    arc_early  = " | ".join(c["subject"] for c in early_commits)  or "early work"
    arc_messy  = " | ".join(c["subject"] for c in messy_commits)  or "pressure period"
    arc_regret = " | ".join(c["subject"] for c in regret_commits) or "late corrections"

    prompt = f"""You are writing a podcast episode script. The style is NPR Planet Money — dry wit, genuine insight, specific details, real human moments. Not a tech explainer. A story about people who built something.

━━━ REPO ━━━
Name: {repo} | Language: {language} | {total} commits | Started: {first}
Top contributors: {authors}
Most-changed files (the ones nobody wants to touch): {files}
README excerpt: {readme[:600]}

━━━ STORY ARC (use this to shape the episode) ━━━
Early excitement: {arc_early}
Pressure period: {arc_messy}
Late corrections: {arc_regret}
Most dramatic commits: {dramatic}

━━━ VOICE BIBLE ━━━

ALEX — HOST
- Warm but not soft. Asks questions that already contain a theory.
- References specific commit messages and file names in every question — never asks "tell me about the project"
- Uses silence as a tool. Short follow-ups after long answers.
- Dry humor, never sarcastic. Genuinely curious about the human story behind the code.
- What Alex never says: "great question", "amazing", "passionate about"

{guest_author} — GUEST
- Tired in an endearing way. Has been maintaining this longer than they planned.
- Answers in two parts: the official version, then the real version.
- Uses "we" for the good decisions, "I" for the ones they regret.
- One line in the interview must be a confession — something they've never said in a PR comment.
- What the guest never says: "it was a learning experience", "we're proud of what we built"

AD NARRATOR
- Full commitment to the bit. Reads fake ad copy like it's a Super Bowl spot.
- Slightly too enthusiastic. The promo code is always a joke.

━━━ PACING MAP (emotional beat per segment) ━━━
Cold Open    → CURIOSITY: a surprising specific fact that makes the listener lean in
Welcome      → MOMENTUM: set up the story arc, name the guest, tease the incident
By Numbers   → WEIGHT: make the stats feel human-scale, not impressive
Interview    → TENSION → CRACK → CONFESSION → QUIET
The Incident → SHOCK: true crime narration of the worst commit
Ad Break     → RELEASE: comedy beat before the ending
Outro        → EARNED: reference what the guest admitted, leave something unresolved

Return ONLY valid JSON, no markdown, no backticks:
{{
  "show_title": "The {repo} Podcast",
  "episode_title": "evocative specific title — like a New Yorker article, not a tech blog post",
  "host_persona": "one sentence: Alex's specific voice, cadence, energy",
  "guest_persona": "one sentence: {guest_author}'s specific voice, tiredness level, emotional register",
  "ad_product": "{ad_product}",
  "segments": [
    {{
      "id": "intro",
      "type": "narration",
      "speaker": "HOST",
      "label": "Cold Open",
      "text": "30-40 words. Start mid-sentence, in media res. One specific surprising fact from the commit history. End with a question the episode will answer. No 'welcome' or 'today on the show'.",
      "live_transcript": "Clean flowing version of this segment's text — no speaker labels, no audio tags, natural punctuation. Short sentences. Tone: curious, slightly urgent. File names and commit references appear naturally."
    }},
    {{
      "id": "welcome",
      "type": "narration",
      "speaker": "HOST",
      "label": "Welcome",
      "text": "50-60 words. Name the show and episode. Reference {guest_author} by first name. Tease the incident. One specific detail from the git history that makes this repo interesting.",
      "live_transcript": "Clean flowing version — tone: warm, building momentum. Human-first, repo details woven in naturally."
    }},
    {{
      "id": "stats",
      "type": "narration",
      "speaker": "HOST",
      "label": "By the Numbers",
      "text": "60-70 words. Rapid-fire stats delivered like a story, not a list. {total} commits. Started {first}. {language}. Name specific files from: {files}. Relate numbers to human scale — not 'impressive' but 'what that actually means'. End on the detail that transitions to the interview.",
      "live_transcript": "Clean flowing version — tone: momentum, weight. Numbers feel human. File names land as story beats."
    }},
    {{
      "id": "interview",
      "type": "dialogue",
      "label": "The Interview",
      "lines": [
        {{"speaker": "HOST", "text": "Opening question that names a specific file or commit from the repo. Not 'tell me about the project'. Theory embedded in the question."}},
        {{"speaker": "GUEST", "text": "[slightly tired] Official answer first, then the real one. References a specific date or file name."}},
        {{"speaker": "HOST", "text": "Sharp follow-up. One sentence. Slightly challenging. References what the guest just said."}},
        {{"speaker": "GUEST", "text": "[hesitates] The answer they'd give at 11pm. Admits something went wrong. Names what broke or had to be redone."}},
        {{"speaker": "HOST", "text": "Pivot to the biggest regret. References a specific commit from: {arc_regret[:60]}."}},
        {{"speaker": "GUEST", "text": "[sighs] The confession. Something they've never said in a PR comment. Fear or overconfidence, not failure."}},
        {{"speaker": "HOST", "text": "One quiet question about what comes next. Genuine, not optimistic."}},
        {{"speaker": "GUEST", "text": "[quietly] Honest. Specific. Neither fully optimistic nor pessimistic. References something real."}}
      ],
      "live_transcript": "The full interview as a single flowing string — no speaker labels, no audio tags. Each line contributes exactly one sentence or fragment. Tone shifts: tense → crack → confession → quiet. File names and commit references appear as natural story details, not technical clutter. Reads like a scene, not a transcript."
    }},
    {{
      "id": "incident",
      "type": "narration",
      "speaker": "HOST",
      "label": "The Incident",
      "text": "60-70 words. Narrate the worst moment in this repo's history like a true crime podcast. Present tense. Short sentences. Reference the actual commit message from: {dramatic[:80] if dramatic else 'a difficult period'}. Build to a reveal. End on the human consequence, not the technical one.",
      "live_transcript": "Clean flowing version — tone: shock, dread. Short punchy sentences. The commit message appears verbatim. Ends on the human cost."
    }},
    {{
      "id": "ad",
      "type": "narration",
      "speaker": "AD",
      "label": "Ad Break",
      "text": "40-50 words. Fake ad for {ad_product} — {ad_pitch}. Full radio announcer sincerity. Genuinely funny. Promo code must be a reference to something specific in this repo.",
      "live_transcript": "Clean flowing version — tone: release, comedy. Reads like a real ad. Promo code lands as the punchline."
    }},
    {{
      "id": "outro",
      "type": "narration",
      "speaker": "HOST",
      "label": "Outro",
      "text": "40-50 words. Reference the guest's confession from the interview. Fake handle @{repo.replace(' ','').lower()}pod. End with something unresolved — not a tidy bow. Ask listeners to subscribe wherever they get podcasts about repos they maintain at 2am.",
      "live_transcript": "Clean flowing version — tone: reflective, earned, slightly unresolved. The confession echoes. Ends quietly."
    }}
  ]
}}

HARD RULES:
- Every segment must name something real: a file, a commit message, an author, a date, a number
- Interview audio tags (max 1 per line): [sighs] [hesitates] [laughs softly] [quietly] [frustrated] [cautiously] [resigned tone] [deadpan]
- Interview lines: max 90 chars each, max 700 chars total
- NO: "amazing work", "passionate about code", "great question", "learning experience", "proud of"
- Episode title must be specific enough that you could not use it for any other repo"""

    gc = oc  # alias for the call below
    for attempt in range(3):
        try:
            resp = oc.chat.completions.create(
                model="gemini-2.5-flash",
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            script = json.loads(raw)
            if "segments" not in script or len(script["segments"]) < 5:
                raise ValueError("Incomplete script")
            return script
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(f"Script generation failed: {e}")
            time.sleep(2)


# ── Step 3: Voice creation ─────────────────────────────────────────────────────

def get_or_create_voice(persona: str, cache_key: str, fallback: str) -> str:
    VOICE_CACHE.mkdir(exist_ok=True)
    cache_file = VOICE_CACHE / f"{cache_key}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())["voice_id"]
        except Exception:
            pass
    try:
        previews = client.text_to_voice.create_previews(
            voice_description=persona,
            text="Today on the show — we're looking at a codebase that has seen some things. Real things."
        )
        if not previews.previews:
            return fallback
        preview = previews.previews[0]
        voice = client.text_to_voice.create_voice_from_preview(
            voice_name=f"pod_{cache_key[:12]}",
            voice_description=persona,
            generated_voice_id=preview.generated_voice_id
        )
        cache_file.write_text(json.dumps({"voice_id": voice.voice_id}))
        return voice.voice_id
    except Exception:
        return fallback


# ── Step 4: Audio generation ──────────────────────────────────────────────────

def _bytes_to_tmp(data: bytes, suffix: str) -> str:
    path = tempfile.mktemp(suffix=suffix, prefix="pod_")
    Path(path).write_bytes(data)
    return path


def _collect(generator) -> bytes:
    return b"".join(chunk for chunk in generator if chunk)


def generate_narration(text: str, voice_id: str) -> str:
    gen = client.text_to_speech.convert(
        text=text, voice_id=voice_id,
        model_id="eleven_v3",
        output_format="mp3_44100_128",
    )
    return _bytes_to_tmp(_collect(gen), "_narration.mp3")


def generate_interview(lines: list, host_voice: str, guest_voice: str) -> str:
    inputs = []
    total_chars = 0
    for line in lines:
        text = line["text"]
        vid  = host_voice if line["speaker"] == "HOST" else guest_voice
        if total_chars + len(text) > 1900:
            break
        inputs.append({"text": text, "voice_id": vid})
        total_chars += len(text)
    gen = client.text_to_dialogue.convert(
        inputs=inputs,
        model_id="eleven_v3",
        output_format="mp3_44100_128",
        seed=42
    )
    return _bytes_to_tmp(_collect(gen), "_interview.mp3")


def generate_music_segment(prompt: str, label: str) -> str | None:
    try:
        gen  = client.music.compose(prompt=prompt)
        data = _collect(gen)
        if not data:
            return None
        return _bytes_to_tmp(data, f"_{label}.mp3")
    except Exception:
        return None


def generate_sfx(description: str, duration_seconds: float = 3.0) -> str | None:
    try:
        gen  = client.text_to_sound_effects.convert(text=description, duration_seconds=duration_seconds)
        data = _collect(gen)
        if not data:
            return None
        return _bytes_to_tmp(data, "_sfx.mp3")
    except Exception:
        return None


# ── Step 5: ffmpeg helpers ────────────────────────────────────────────────────

def get_duration_ms(path: str) -> int:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True
    )
    try:
        return int(float(json.loads(r.stdout)["format"]["duration"]) * 1000)
    except Exception:
        return 0


def mix_under(foreground: str, background: str, bg_volume: float = 0.10) -> str:
    out    = tempfile.mktemp(suffix="_mixed.mp3", prefix="pod_")
    fg_dur = get_duration_ms(foreground)
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", foreground, "-i", background,
            "-filter_complex",
            f"[1:a]volume={bg_volume},aloop=loop=-1:size=2e+09[bg];"
            f"[bg]atrim=duration={fg_dur/1000}[bg_trimmed];"
            f"[0:a][bg_trimmed]amix=inputs=2:duration=first:normalize=0[out]",
            "-map", "[out]", "-codec:a", "libmp3lame", "-b:a", "128k", out
        ], capture_output=True, check=True)
        return out
    except Exception:
        return foreground


def concat_audio(paths: list) -> str:
    valid = [p for p in paths if p and os.path.exists(p)]
    if not valid:
        raise RuntimeError("No audio segments to concatenate")
    if len(valid) == 1:
        return valid[0]
    listfile = tempfile.mktemp(suffix="_list.txt", prefix="pod_")
    out      = tempfile.mktemp(suffix="_episode.mp3", prefix="pod_")
    with open(listfile, "w") as f:
        for p in valid:
            f.write(f"file '{p}'\n")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", listfile, "-codec:a", "libmp3lame", "-b:a", "128k", out
    ], capture_output=True, check=True)
    os.unlink(listfile)
    return out


def add_chapter_markers(input_mp3: str, chapters: list) -> str:
    meta_file = tempfile.mktemp(suffix="_meta.txt", prefix="pod_")
    out       = tempfile.mktemp(suffix="_chaptered.mp3", prefix="pod_")
    lines = [";FFMETADATA1\n"]
    for ch in chapters:
        lines += ["[CHAPTER]\n", "TIMEBASE=1/1000\n",
                  f"START={ch['start_ms']}\n", f"END={ch['end_ms']}\n",
                  f"title={ch['title']}\n", "\n"]
    with open(meta_file, "w") as f:
        f.writelines(lines)
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", input_mp3, "-i", meta_file,
            "-map_metadata", "1", "-codec:a", "copy", out
        ], capture_output=True, check=True)
        os.unlink(meta_file)
        return out
    except Exception:
        os.unlink(meta_file)
        return input_mp3


# ── Step 6: Play ──────────────────────────────────────────────────────────────

def play_audio(path: str):
    for cmd in [["mpv", "--no-video", "--really-quiet", path], ["afplay", path], ["aplay", path]]:
        try:
            subprocess.run(["which", cmd[0]], capture_output=True, check=True)
            subprocess.Popen(cmd)
            return
        except Exception:
            continue
    try:
        os.startfile(path)
    except AttributeError:
        subprocess.Popen(["open", path])


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    json_mode  = "--json" in sys.argv
    args       = sys.argv[1:]
    github_url = None
    repo_dir   = None

    if "--github" in args:
        idx = args.index("--github")
        if idx + 1 < len(args):
            github_url = args[idx + 1]
    if "--dir" in args:
        idx = args.index("--dir")
        if idx + 1 < len(args):
            repo_dir = args[idx + 1]

    if not github_url and not repo_dir:
        if not json_mode:
            print("Usage:")
            print("  python scripts/podcast.py --github https://github.com/user/repo")
            print("  python scripts/podcast.py --dir /path/to/local/repo")
        sys.exit(1)

    if not json_mode:
        print("\n🎙️  ECHOES PODCAST")
        print("─" * 50)

    # 1. Get repo
    if github_url:
        log(f"\n[1/8] Cloning {github_url}...", json_mode)
        try:
            repo_dir = clone_github_repo(github_url)
        except Exception as e:
            msg = f"Clone failed: {e}"
            if json_mode: print(json.dumps({"error": msg}))
            else: print(f"  ERROR: {msg}")
            sys.exit(1)
    else:
        log(f"\n[1/8] Reading repo at {repo_dir}...", json_mode)

    # 2. Read data
    log("[2/8] Reading git history, README, file structure...", json_mode)
    try:
        data = read_repo_data(repo_dir)
    except Exception as e:
        if json_mode: print(json.dumps({"error": str(e)}))
        else: print(f"  ERROR: {e}")
        sys.exit(1)

    if not data["total_commits"]:
        msg = "No commits found in this repo."
        if json_mode: print(json.dumps({"error": msg}))
        else: print(f"  {msg}")
        sys.exit(0)

    if not json_mode:
        print(f"  → {data['total_commits']} commits  ·  {len(data['top_authors'])} contributors  ·  {data['language']}")

    # 3. Generate script
    log("\n[3/8] Writing podcast script (Gemini 2.5 Flash)...", json_mode)
    try:
        script = generate_podcast_script(data, json_mode)
    except Exception as e:
        if json_mode: print(json.dumps({"error": str(e)}))
        else: print(f"  ERROR: {e}")
        sys.exit(1)

    if not json_mode:
        print(f"\n  Episode: \"{script.get('episode_title', 'Untitled')}\"")
        print(f"  Host:    Alex  ({script.get('host_persona', '')[:60]})")
        guest_name = data['top_authors'][0][0] if data['top_authors'] else 'Guest'
        print(f"  Guest:   {guest_name}  ({script.get('guest_persona', '')[:60]})")
        print(f"  Segments: {len(script.get('segments', []))}")

    # 4. Create voices
    log("\n[4/8] Creating voices via Voice Design...", json_mode)
    host_seed  = "podcast_host_alex"
    guest_name = data["top_authors"][0][0] if data["top_authors"] else "guest"
    guest_seed = hashlib.md5(guest_name.encode()).hexdigest()[:8]

    host_voice  = get_or_create_voice(script.get("host_persona", "warm NPR podcast host, dry wit"), host_seed, FALLBACK_HOST)
    guest_voice = get_or_create_voice(script.get("guest_persona", "software engineer, slightly tired, earnest"), f"guest_{guest_seed}", FALLBACK_GUEST)
    ad_voice    = FALLBACK_AD

    if not json_mode:
        print(f"  → Host:  {host_voice}")
        print(f"  → Guest: {guest_voice}")
        print(f"  → Ad:    {ad_voice} (library voice)")

    # 5. Generate music assets
    log("\n[5/8] Generating music (jingle + stings + background)...", json_mode)
    jingle      = generate_music_segment("upbeat tech podcast intro jingle, 8 seconds, electronic, professional, NPR-style", "jingle")
    outro_music = generate_music_segment("warm podcast outro music, fade out, 8 seconds, optimistic, gentle", "outro")
    bg_music    = generate_music_segment("subtle background music for podcast interview, low energy, ambient, barely audible, loopable", "background")
    sting       = generate_sfx("short podcast transition sound, 1 second, soft chime", 1.5)

    if not json_mode:
        for name, path in [("Intro jingle", jingle), ("Outro music", outro_music), ("BG music", bg_music), ("Transition sting", sting)]:
            print(f"  {'✓' if path else '✗'} {name}")

    # 6. Record segments
    log("\n[6/8] Recording all segments...", json_mode)
    segments_audio = []
    chapters_raw   = []

    for seg in script.get("segments", []):
        seg_id    = seg.get("id", "seg")
        seg_label = seg.get("label", seg_id)
        seg_type  = seg.get("type", "narration")
        log(f"  Recording: {seg_label}...", json_mode)
        try:
            if seg_type == "dialogue":
                audio = generate_interview(seg.get("lines", []), host_voice, guest_voice)
            else:
                text  = seg.get("text", "")
                if not text:
                    continue
                speaker = seg.get("speaker", "HOST")
                voice   = host_voice if speaker == "HOST" else (ad_voice if speaker == "AD" else guest_voice)
                audio   = generate_narration(text, voice)
                if bg_music and seg_id not in ("intro", "ad"):
                    audio = mix_under(audio, bg_music, bg_volume=0.06)

            chapters_raw.append({"title": seg_label, "path": audio})
            segments_audio.append(audio)
            if sting and seg_id != "outro":
                segments_audio.append(sting)
        except Exception as e:
            log(f"  ✗ {seg_label} failed: {e}", json_mode)
            continue

    # 7. Assemble
    log("\n[7/8] Assembling episode...", json_mode)
    full_sequence = []
    if jingle:      full_sequence.append(jingle)
    full_sequence  += segments_audio
    if outro_music: full_sequence.append(outro_music)

    try:
        raw_episode = concat_audio([p for p in full_sequence if p])
    except Exception as e:
        if json_mode: print(json.dumps({"error": f"Assembly failed: {e}"}))
        else: print(f"  ERROR: {e}")
        sys.exit(1)

    # 8. Chapter markers
    log("[8/8] Adding chapter markers...", json_mode)
    chapters  = []
    cursor_ms = get_duration_ms(jingle) if jingle else 0
    for item in chapters_raw:
        dur = get_duration_ms(item["path"])
        chapters.append({"title": item["title"], "start_ms": cursor_ms, "end_ms": cursor_ms + dur})
        cursor_ms += dur + (get_duration_ms(sting) if sting else 0)

    final_path = add_chapter_markers(raw_episode, chapters)
    if final_path != raw_episode and os.path.exists(raw_episode):
        try: os.unlink(raw_episode)
        except Exception: pass

    total_ms  = get_duration_ms(final_path)
    total_min = total_ms // 60000
    total_sec = (total_ms % 60000) // 1000

    if json_mode:
        print(json.dumps({
            "success": True, "audio_path": final_path,
            "episode_title": script.get("episode_title", ""),
            "show_title": script.get("show_title", ""),
            "duration_ms": total_ms,
            "chapters": [{"title": c["title"], "start_ms": c["start_ms"]} for c in chapters],
            "repo_name": data["repo_name"], "segments": len(segments_audio),
            "script_segments": script.get("segments", []),
        }))
    else:
        print(f"\n✅  Episode ready")
        print(f"   File:     {final_path}")
        print(f"   Duration: {total_min}:{total_sec:02d}")
        print(f"   Chapters: {len(chapters)}")
        print()
        for i, ch in enumerate(chapters):
            ms = ch["start_ms"]
            print(f"   {i+1:02d}  {ms//60000}:{(ms%60000)//1000:02d}  {ch['title']}")
        print("\n   Playing now...\n")
        play_audio(final_path)

    return final_path


if __name__ == "__main__":
    main()
