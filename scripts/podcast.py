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
    from google import genai
    from google.genai import types

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

    prompt = f"""You are writing the script for a professional tech podcast episode — think NPR Planet Money meets a developer conference talk. Dry wit, genuine insight, real human moments.

REPO: {repo} | Language: {language} | Commits: {total} | Started: {first}
Contributors: {authors}
Most changed files: {files}
README: {readme}
Recent commits: {recent}
Dramatic commits: {dramatic}

HOST: Alex — sharp, warm, NPR weekend edition energy. Knows enough to ask real questions. Has opinions.
GUEST: {guest_author} — top contributor. Tired in an endearing way. Defensive when challenged, honest when pressed.
AD NARRATOR: classic, slightly-too-enthusiastic radio announcer.

Return ONLY valid JSON, no markdown:
{{
  "show_title": "The {repo} Podcast",
  "episode_title": "creative specific episode title — NOT generic",
  "host_persona": "one specific sentence about Alex voice and energy",
  "guest_persona": "one specific sentence about {guest_author} voice and energy",
  "ad_product": "{ad_product}",
  "segments": [
    {{
      "id": "intro",
      "type": "narration",
      "speaker": "HOST",
      "label": "Cold Open",
      "text": "30-40 words. Start mid-thought, in media res. A specific surprising fact about this repo. End with a question the episode will answer."
    }},
    {{
      "id": "welcome",
      "type": "narration",
      "speaker": "HOST",
      "label": "Welcome",
      "text": "50-60 words. Name the show, the episode, tease what is coming. One specific detail from git history. Reference top contributor by first name."
    }},
    {{
      "id": "stats",
      "type": "narration",
      "speaker": "HOST",
      "label": "By the Numbers",
      "text": "60-70 words. Rapid-fire stats conversationally. {total} commits. Started {first}. {language}. Reference specific files from: {files}. Make numbers feel human-scale. End on a detail that transitions to the interview."
    }},
    {{
      "id": "interview",
      "type": "dialogue",
      "label": "The Interview",
      "lines": [
        {{"speaker": "HOST", "text": "Opening question referencing a real commit or file. Not 'tell me about the project'."}},
        {{"speaker": "GUEST", "text": "[slightly tired] Answer revealing something real. References actual commit dates or file names."}},
        {{"speaker": "HOST", "text": "Sharp follow-up. Slightly challenging."}},
        {{"speaker": "GUEST", "text": "[hesitates] Honest answer. The one they give at 11pm not 11am."}},
        {{"speaker": "HOST", "text": "Pivot to the biggest regret."}},
        {{"speaker": "GUEST", "text": "[sighs] The real answer. References something from: {dramatic_snippet}."}},
        {{"speaker": "HOST", "text": "One last question about what comes next."}},
        {{"speaker": "GUEST", "text": "[quietly] Honest about the future. Specific. Neither fully optimistic nor pessimistic."}}
      ]
    }},
    {{
      "id": "incident",
      "type": "narration",
      "speaker": "HOST",
      "label": "The Incident",
      "text": "60-70 words. Narrate the most dramatic moment in this repo's history like a true crime podcast. Reference actual commit messages. Present tense. Dramatic."
    }},
    {{
      "id": "ad",
      "type": "narration",
      "speaker": "AD",
      "label": "Ad Break",
      "text": "40-50 words. Fake ad for {ad_product} — {ad_pitch}. Full radio announcer sincerity. Genuinely funny. End with a fake promo code referencing something in this repo."
    }},
    {{
      "id": "outro",
      "type": "narration",
      "speaker": "HOST",
      "label": "Outro",
      "text": "40-50 words. Wrap up. Reference something the guest said. Fake handle @{repo.replace(' ','').lower()}pod. Ask listeners to subscribe wherever they get podcasts about repos they maintain at 2am."
    }}
  ]
}}

RULES:
- Every segment must reference something SPECIFIC — real file name, real commit, real author name
- Interview audio tags only: [sighs] [hesitates] [laughs softly] [quietly] [frustrated] [cautiously] [resigned tone] [deadpan]
- Max 2 audio tags per interview line, max 80 chars per line, max 600 chars total across all interview lines
- NO generic phrases like "amazing work", "passionate about code", "great question"
- Episode title should be evocative and specific — like a New Yorker article title"""

    gc = genai.Client(api_key=GEMINI_API_KEY)
    for attempt in range(3):
        try:
            resp = gc.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            script = json.loads(resp.text.strip())
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
