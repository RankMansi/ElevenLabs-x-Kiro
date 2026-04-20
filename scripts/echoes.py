#!/usr/bin/env python3
"""
Echoes — Git History as Radio Drama
Run:               python scripts/echoes.py
Run (web server):  python scripts/echoes.py --json
Point at any repo: python scripts/echoes.py --dir /path/to/repo
"""

import sys, os, json, subprocess, hashlib, tempfile
from pathlib import Path
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY")

if not ELEVENLABS_API_KEY:
    print(json.dumps({"error": "ELEVENLABS_API_KEY not set in .env"})); sys.exit(1)
if not GEMINI_API_KEY:
    print(json.dumps({"error": "GEMINI_API_KEY not set in .env"})); sys.exit(1)

client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
VOICE_AUTHOR    = "JBFqnCBsd6RMkjVDRZzb"
VOICE_READER    = "EXAVITQu4vr4xnSDxMaL"
VOICE_CACHE_DIR = Path(".echoes_voices")


def get_repo_history(repo_dir=None):
    cwd = os.path.abspath(repo_dir) if repo_dir else os.getcwd()
    check = subprocess.run(["git","rev-parse","--git-dir"], capture_output=True, text=True, cwd=cwd)
    if check.returncode != 0:
        print(f"ERROR: Not a git repo: {cwd}", file=sys.stderr); return [], {}

    try:
        r = subprocess.run(["git","log","--format=%an|||%ae|||%ad|||%s","--date=relative"],
                           capture_output=True, text=True, cwd=cwd, timeout=20)
    except subprocess.TimeoutExpired:
        return [], {}

    commits = []
    for line in r.stdout.strip().split("\n"):
        if "|||" not in line: continue
        p = line.split("|||")
        if len(p) >= 4:
            commits.append({"author":p[0].strip(),"email":p[1].strip(),
                            "date":p[2].strip(),"subject":p[3].strip()})

    author_counts = {}
    for c in commits:
        author_counts[c["author"]] = author_counts.get(c["author"], 0) + 1
    top_authors = sorted(author_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    try:
        fc = subprocess.run(
            ["git","log","--name-only","--format=","--","*.py","*.js","*.ts","*.go","*.rs","*.rb"],
            capture_output=True, text=True, cwd=cwd, timeout=10)
        file_counts = {}
        for f in fc.stdout.strip().split("\n"):
            f = f.strip()
            if f: file_counts[f] = file_counts.get(f, 0) + 1
        hot_files = sorted(file_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    except Exception:
        hot_files = []

    try:
        first = subprocess.run(["git","log","--reverse","--format=%ar","--"],
                               capture_output=True, text=True, cwd=cwd).stdout.strip().split("\n")[0]
    except Exception:
        first = "a long time ago"

    stats = {
        "repo_name":     Path(cwd).name,
        "total_commits": len(commits),
        "top_authors":   top_authors,
        "hot_files":     hot_files,
        "first_commit":  first,
        "repo_dir":      cwd,
    }
    return commits[:30], stats


def generate_repo_drama_script(commits, stats):
    from google import genai
    from google.genai import types

    # gemini-2.0-flash: free tier, 1500 req/day, no billing required
    MODEL = "gemini-2.5-flash"

    gc          = genai.Client(api_key=GEMINI_API_KEY)
    repo_name   = stats.get("repo_name", "this repo")
    total       = stats.get("total_commits", len(commits))
    top_authors = stats.get("top_authors", [])
    hot_files   = stats.get("hot_files", [])
    first       = stats.get("first_commit", "a long time ago")
    authors_str = ", ".join([f"{a} ({n} commits)" for a,n in top_authors[:4]])
    files_str   = ", ".join([f[0] for f in hot_files[:4]])
    first_file  = hot_files[0][0] if hot_files else "that file"
    a1 = top_authors[0][0] if top_authors else "Lead Dev"
    a2 = top_authors[1][0] if len(top_authors) > 1 else "Second Dev"

    sample, step = [], max(1, len(commits) // 10)
    for i in range(0, len(commits), step):
        sample.append(commits[i])
    sample_str = "\n".join([f"- {c['date']}: [{c['author']}] {c['subject']}" for c in sample[:15]])

    prompt = f"""You are a playwright writing a 60-second radio drama. Not a code review. Not a retrospective. A DRAMA.

The setting: two engineers who built '{repo_name}' together are having a raw, unfiltered conversation at 11pm after a bad production incident. They've had one drink. {total} commits. Started {first}. Exhausted. Finally saying what they actually think.

Repo facts:
- Top contributors: {authors_str}
- Most-changed files: {files_str}
- Real commits across the project's life:
{sample_str}

AUTHOR = {a1}. Most commits. Built the foundation. Proud but haunted by every shortcut they took. Gets defensive when challenged, then quietly honest.

READER = {a2}. Joined later. Inherited the mess. Respects {a1} but is genuinely frustrated — maintains code whose reasoning died in Slack threads that were never written down.

RULES FOR GREAT DRAMA:

1. SPECIFICITY OVER GENERALITY.
   Bad: "the architecture feels brittle."
   Good: "[frustrated] Why does {first_file} import from three different places? Pick one."
   Name real files. Quote real commit messages. Use real author names.

2. EMOTIONAL ESCALATION. Surface frustration → crack open into something real by line 8.
   AUTHOR must have one genuine admission they've never said out loud.
   READER must have one moment where they almost apologize for pushing too hard.

3. SUBTEXT. The real question under all of it: "Did we make something worth making?"
   Never say this out loud. Let it live beneath every line.

4. VOICE DISTINCTION.
   AUTHOR: justifications that slowly unravel under pressure.
   READER: questions that already contain the answer.

5. THE TURN. Around line 7-8, a real admission stops the argument cold.
   Something that could never go in a pull request comment.

EXAMPLES OF THE EXACT TONE:
- "[quietly] I knew the eval runner was wrong when I shipped it. I just needed the demo to work."
- "[laughs softly] You spent three weeks on that abstraction. I merged it in ten minutes."
- "[hesitates] The commits don't show how scared we were that none of it would matter."
- "[resigned tone] We kept saying we'd come back and fix it. We never came back."
- "[frustrated] Every 'quick fix' commit is a scar. There are forty-three of them."

FORBIDDEN — these will make it bad:
- Generic tech words: "brittle", "pile on", "core design", "technical debt"
- Ending with a vague philosophical question like "what now?"
- Corporate retrospective language
- Any line that could appear in a JIRA ticket or Confluence page
- Anything theoretical — every single line must be visceral and specific

Return ONLY valid JSON, no markdown, no backticks:
{{
  "author_persona": "one sentence: voice, age, energy, emotional register — be specific (e.g. 'a tired 40-year-old with a slight accent who speaks in short declarative sentences when cornered')",
  "reader_persona": "one sentence: voice, age, energy, emotional register — be specific",
  "lines": [
    {{"speaker": "AUTHOR", "text": "dialogue with inline v3 audio tags"}},
    {{"speaker": "READER", "text": "dialogue with inline v3 audio tags"}}
  ]
}}

HARD RULES:
1. Exactly 10-12 lines
2. Max 1800 total chars across ALL text values — punchy, not wordy
3. Audio tags ONLY from: [sighs] [frustrated] [resigned tone] [laughing] [whispers]
   [cautiously] [cheerfully] [deadpan] [giggling] [groaning] [hesitates] [sarcastic]
   [jumping in] [elated] [quizzically] [quietly] [softly] [laughs softly]
4. Interruption: end line with dash "-"
5. Trailing thought: end with "..."
6. speaker must be exactly "AUTHOR" or "READER"
7. Max 2 audio tags per line
8. Every line references something real — a filename, a commit message, an author name, a date"""

    for attempt in range(3):
        try:
            resp = gc.models.generate_content(
                model=MODEL, contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            script = json.loads(resp.text.strip())
            if "lines" not in script: raise ValueError("No lines")
            total_chars = sum(len(l["text"]) for l in script["lines"])
            while total_chars > 1900 and len(script["lines"]) > 4:
                script["lines"].pop()
                total_chars = sum(len(l["text"]) for l in script["lines"])
            return script
        except Exception as e:
            if attempt == 2: raise
    raise RuntimeError("Script generation failed")


def get_or_create_voice(persona, seed_key, default_voice):
    VOICE_CACHE_DIR.mkdir(exist_ok=True)
    cache = VOICE_CACHE_DIR / f"{seed_key}.json"
    if cache.exists():
        try: return json.loads(cache.read_text())["voice_id"]
        except Exception: pass
    try:
        previews = client.text_to_voice.create_previews(
            voice_description=persona,
            text="I just needed it to work by Friday. The tech debt was a calculated risk."
        )
        if not previews.previews: return default_voice
        preview = previews.previews[0]
        voice = client.text_to_voice.create_voice_from_preview(
            voice_name=f"echoes_{seed_key}", voice_description=persona,
            generated_voice_id=preview.generated_voice_id
        )
        cache.write_text(json.dumps({"voice_id": voice.voice_id}))
        return voice.voice_id
    except Exception:
        return default_voice


def generate_dialogue(lines, author_voice, reader_voice):
    inputs = [{"text": l["text"],
               "voice_id": author_voice if l["speaker"]=="AUTHOR" else reader_voice}
              for l in lines]
    gen = client.text_to_dialogue.convert(
        inputs=inputs, model_id="eleven_v3", output_format="mp3_44100_128", seed=42)
    audio = b"".join(c for c in gen if c)
    if not audio: raise RuntimeError("Text-to-Dialogue returned empty audio")
    path = tempfile.mktemp(suffix="_dialogue.mp3", prefix="echoes_")
    Path(path).write_bytes(audio)
    return path


def generate_bg_music():
    try:
        gen = client.music.compose(
            prompt="low tension ambient office atmosphere, subtle drone, cinematic, 60 seconds")
        audio = b"".join(c for c in gen if c)
        if not audio: return None
        path = tempfile.mktemp(suffix="_music.mp3", prefix="echoes_")
        Path(path).write_bytes(audio)
        return path
    except Exception:
        return None


def mix_and_export(dialogue_path, music_path):
    out = tempfile.mktemp(suffix="_final.mp3", prefix="echoes_")
    if not music_path:
        Path(out).write_bytes(Path(dialogue_path).read_bytes()); return out
    try:
        subprocess.run(["ffmpeg","-version"], capture_output=True, check=True)
        r = subprocess.run([
            "ffmpeg","-y","-i",dialogue_path,"-i",music_path,
            "-filter_complex","[1:a]volume=0.12[bg];[0:a][bg]amix=inputs=2:duration=first:normalize=0[out]",
            "-map","[out]","-codec:a","libmp3lame","-b:a","128k",out
        ], capture_output=True, text=True)
        if r.returncode != 0:
            Path(out).write_bytes(Path(dialogue_path).read_bytes())
    except Exception:
        Path(out).write_bytes(Path(dialogue_path).read_bytes())
    return out


def play_audio(path):
    for cmd in [["mpv","--no-video","--really-quiet",path],["afplay",path],["aplay",path]]:
        try:
            subprocess.run(["which",cmd[0]], capture_output=True, check=True)
            subprocess.Popen(cmd); return
        except Exception: continue
    try: os.startfile(path)
    except AttributeError: subprocess.Popen(["open",path])


def main():
    json_mode = "--json" in sys.argv
    repo_dir  = None
    args      = sys.argv[1:]
    if "--dir" in args:
        idx = args.index("--dir")
        if idx + 1 < len(args):
            repo_dir = args[idx + 1]
        else:
            print("ERROR: --dir requires a path", file=sys.stderr); sys.exit(1)

    target = repo_dir if repo_dir else "current directory"
    if not json_mode:
        print(f"\n🎭 ECHOES — {target}"); print("─" * 50)

    if not json_mode: print("\n[1/5] Scanning all repo commits...")
    commits, stats = get_repo_history(repo_dir)
    if not commits:
        msg = f"No git commits found in: {target}. Pass --dir /path/to/repo to target a specific repo."
        if json_mode: print(json.dumps({"error": msg}))
        else: print(f"\n  {msg}")
        sys.exit(0)

    if not json_mode:
        print(f"  → {stats['total_commits']} total commits")
        print(f"  → Top authors: {', '.join(a for a,_ in stats['top_authors'][:3])}")
        print(f"  → Hot files:   {', '.join(f for f,_ in stats['hot_files'][:3])}")

    if not json_mode: print("\n[2/5] Writing drama script...")
    try:
        script = generate_repo_drama_script(commits, stats)
    except Exception as e:
        if json_mode: print(json.dumps({"error": str(e)}))
        else: print(f"ERROR: {e}")
        sys.exit(1)

    if not json_mode:
        print("\n" + "─"*50)
        for l in script["lines"]:
            print(f"  {'AUTHOR →' if l['speaker']=='AUTHOR' else 'READER →'}  {l['text']}")
        print("─"*50)

    unique_authors = [a for a,_ in stats["top_authors"]]

    if not json_mode: print("\n[3/5] Selecting voices...")
    a_seed  = hashlib.md5((unique_authors[0] if unique_authors else "author").encode()).hexdigest()[:8]
    r_seed  = hashlib.md5((unique_authors[1] if len(unique_authors)>1 else "reader").encode()).hexdigest()[:8]
    a_voice = get_or_create_voice(script.get("author_persona","gruff experienced engineer"), f"author_{a_seed}", VOICE_AUTHOR)
    r_voice = get_or_create_voice(script.get("reader_persona","curious methodical engineer"), f"reader_{r_seed}", VOICE_READER)

    if not json_mode: print("\n[4/5] Calling ElevenLabs Text-to-Dialogue...")
    try:
        dialogue_path = generate_dialogue(script["lines"], a_voice, r_voice)
    except Exception as e:
        if json_mode: print(json.dumps({"error": str(e)}))
        else: print(f"ERROR: {e}")
        sys.exit(1)

    if not json_mode: print("\n[5/5] Generating score & mixing...")
    music_path = generate_bg_music()
    final_path = mix_and_export(dialogue_path, music_path)

    for tmp in [dialogue_path, music_path]:
        if tmp and tmp != final_path and os.path.exists(tmp):
            try: os.unlink(tmp)
            except Exception: pass

    if json_mode:
        print(json.dumps({
            "success":True, "lines":script["lines"], "authors":unique_authors,
            "commit_count":stats["total_commits"], "audio_path":final_path,
            "char_count":sum(len(l["text"]) for l in script["lines"]),
            "label":stats.get("repo_name","repo")
        }))
    else:
        print(f"\n✅  ECHOES done → {final_path}")
        play_audio(final_path)
        print("    Re-run for a different take.\n")

    return final_path


if __name__ == "__main__":
    main()