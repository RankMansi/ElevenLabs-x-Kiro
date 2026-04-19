#!/usr/bin/env python3
"""
Echoes — Git History as Radio Drama
Turns any file's git log into a voiced debate between the original engineers.

APIs used:
- ElevenLabs Text-to-Dialogue: POST /v1/text-to-dialogue (model: eleven_v3)
- ElevenLabs Music API: compose background score
- Google Gemini 2.5 Flash: write the drama script (free tier)
- ffmpeg: mix dialogue + music
- mpv: play the final audio
"""

import sys
import os
import json
import subprocess
import hashlib
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

load_dotenv()

# ─── Configuration ────────────────────────────────────────────────────────────

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY")

if not ELEVENLABS_API_KEY:
    print("ERROR: ELEVENLABS_API_KEY not set in .env", file=sys.stderr)
    sys.exit(1)

if not GEMINI_API_KEY:
    print("ERROR: GEMINI_API_KEY not set in .env", file=sys.stderr)
    sys.exit(1)

client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

# Two contrasting ElevenLabs library voices
# AUTHOR = the gruff, experienced engineer who wrote the original code
# READER = the curious, questioning engineer investigating it now
VOICE_AUTHOR = "JBFqnCBsd6RMkjVDRZzb"  # George — British, authoritative
VOICE_READER = "EXAVITQu4vr4xnSDxMaL"  # Bella — clear, inquisitive

VOICE_CACHE_DIR = Path(".echoes_voices")


# ─── Step 1: Parse git history ─────────────────────────────────────────────────

def get_git_history(filepath: str) -> list[dict]:
    """
    Runs git log on the file and returns a list of commit dicts.
    Format: author, email, date (relative), subject, body.
    """
    cmd = [
        "git", "log",
        "--follow",
        "--format=%an|||%ae|||%ad|||%s|||%b",
        "--date=relative",
        "--",
        filepath
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=os.getcwd(),
            timeout=15
        )
    except subprocess.TimeoutExpired:
        print("WARNING: git log timed out", file=sys.stderr)
        return []
    except FileNotFoundError:
        print("ERROR: git not found. Is git installed?", file=sys.stderr)
        sys.exit(1)

    commits = []
    for line in result.stdout.strip().split("\n"):
        if "|||" not in line:
            continue
        parts = line.split("|||")
        if len(parts) >= 4:
            commits.append({
                "author":  parts[0].strip(),
                "email":   parts[1].strip(),
                "date":    parts[2].strip(),
                "subject": parts[3].strip(),
                "body":    parts[4].strip() if len(parts) > 4 else ""
            })

    return commits[:20]  # cap at 20 most recent


def get_file_blame_summary(filepath: str) -> str:
    """Get a short git blame summary for context."""
    try:
        result = subprocess.run(
            ["git", "blame", "--line-porcelain", filepath],
            capture_output=True, text=True, timeout=10
        )
        # Count commits per author from blame
        author_lines = {}
        for line in result.stdout.split("\n"):
            if line.startswith("author "):
                name = line.replace("author ", "").strip()
                author_lines[name] = author_lines.get(name, 0) + 1
        if author_lines:
            top = sorted(author_lines.items(), key=lambda x: x[1], reverse=True)[:3]
            return ", ".join([f"{name} ({lines} lines)" for name, lines in top])
    except Exception:
        pass
    return "unknown authors"


# ─── Step 2: Generate drama script via LLM ────────────────────────────────────

def generate_drama_script(filepath: str, commits: list[dict]) -> dict:
    """
    Sends git history to Gemini 2.5 Flash and gets back a JSON script
    formatted for the ElevenLabs Text-to-Dialogue API.
    """
    import google.generativeai as genai

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        generation_config={"response_mime_type": "application/json"}
    )

    filename = Path(filepath).name
    history_str = "\n".join([
        f"- {c['date']}: [{c['author']}] {c['subject']}"
        + (f" — {c['body'][:80]}" if c['body'] else "")
        for c in commits[:12]
    ])

    unique_authors = list(dict.fromkeys([c['author'] for c in commits]))
    author1 = unique_authors[0] if unique_authors else "Original Dev"
    author2 = unique_authors[1] if len(unique_authors) > 1 else "Dev"

    prompt = f"""You are writing a 60-second cinematic radio drama about the git history of a source code file.

File: {filename}
Git history (most recent first):
{history_str}

Two speakers:
- AUTHOR: represents {author1} — the engineer(s) who originally wrote this code. They were under pressure, had reasons for their decisions, feel defensive but also regret some choices.
- READER: represents {author2} — the engineer reading the code now, confused, investigating a bug or trying to understand the design.

Write a tense, emotionally real argument between them. Make it feel like the AUTHOR is speaking from memory — they remember the context, the deadline pressure, the trade-offs. Make the READER skeptical but not cruel.

Return ONLY valid JSON — absolutely no markdown, no code fences, no preamble:
{{
  "author_persona": "one sentence describing the AUTHOR's vocal personality for voice design",
  "reader_persona": "one sentence describing the READER's vocal personality for voice design",
  "lines": [
    {{"speaker": "AUTHOR", "text": "[sighs] Look, we had a reason for this-"}},
    {{"speaker": "READER", "text": "[cautiously] What reason? The comment just says 'TODO: fix later'."}}
  ]
}}

STRICT RULES — violating any will break the ElevenLabs API:
1. Maximum 12 lines total
2. Maximum 1800 characters total across ALL text values combined (count carefully)
3. Use ONLY these audio tags (from ElevenLabs v3 docs): [sighs] [frustrated] [resigned tone] [laughing] [whispers] [cautiously] [cheerfully] [deadpan] [giggling] [groaning] [hesitates] [sarcastic] [jumping in] [elated] [quizzically]
4. Interruptions: end a line with a dash like "I thought we could just-"
5. Trailing sentences: end with "..." like "Well, the thing is..."
6. Reference actual commit messages — ground it in real history
7. The drama must feel earned, not performed. Real engineers, real pressure.
8. No more than 2 audio tags per line
9. Speaker values must be exactly "AUTHOR" or "READER" (uppercase)"""

    print("  → Calling Gemini 2.5 Flash to write drama script...")

    for attempt in range(3):
        try:
            resp = model.generate_content(prompt)
            raw = resp.text.strip()
            script = json.loads(raw)

            # Validate structure
            if "lines" not in script or not isinstance(script["lines"], list):
                raise ValueError("Missing 'lines' array in response")

            # Enforce char limit
            total_chars = sum(len(l["text"]) for l in script["lines"])
            if total_chars > 2000:
                print(f"  WARNING: {total_chars} chars — truncating to fit 2000 char limit")
                # Trim lines until under limit
                while total_chars > 1900 and len(script["lines"]) > 4:
                    script["lines"].pop()
                    total_chars = sum(len(l["text"]) for l in script["lines"])

            return script

        except (json.JSONDecodeError, ValueError) as e:
            print(f"  Attempt {attempt+1} failed: {e} — retrying...")
            if attempt == 2:
                raise RuntimeError("Failed to get valid script from Gemini after 3 attempts")

    raise RuntimeError("Script generation failed")


# ─── Step 3: Voice Design (optional upgrade) ──────────────────────────────────

def get_or_create_voice(persona_description: str, seed_key: str, default_voice_id: str) -> str:
    """
    Returns a voice_id. Uses Voice Design API to create a unique synthetic
    voice if not cached. Falls back to default_voice_id on any error.

    Voice Design creates a voice from a text description — each author
    gets a unique voice that matches their personality from commit history.
    """
    VOICE_CACHE_DIR.mkdir(exist_ok=True)
    cache_file = VOICE_CACHE_DIR / f"{seed_key}.json"

    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            print(f"  → Using cached voice for seed {seed_key}: {data['voice_id']}")
            return data["voice_id"]
        except Exception:
            pass

    try:
        print(f"  → Generating Voice Design for: {persona_description[:60]}...")
        # Use Voice Design API to create a synthetic voice from description
        previews = client.text_to_voice.create_previews(
            voice_description=persona_description,
            text="I just needed it to work by Friday. The tech debt was a calculated risk, not a mistake."
        )

        if not previews.previews:
            print("  WARNING: No voice previews returned — using default voice")
            return default_voice_id

        # Save the first preview as a new voice
        preview = previews.previews[0]
        voice = client.text_to_voice.create_voice_from_preview(
            voice_name=f"echoes_{seed_key}",
            voice_description=persona_description,
            generated_voice_id=preview.generated_voice_id
        )

        cache_file.write_text(json.dumps({"voice_id": voice.voice_id}))
        print(f"  → Voice Design created: {voice.voice_id}")
        return voice.voice_id

    except Exception as e:
        print(f"  WARNING: Voice Design failed ({e}) — using default voice")
        return default_voice_id


# ─── Step 4: Text-to-Dialogue API call ───────────────────────────────────────

def generate_dialogue(
    script_lines: list[dict],
    author_voice_id: str,
    reader_voice_id: str
) -> str:
    """
    Calls ElevenLabs Text-to-Dialogue API.

    Endpoint: POST /v1/text-to-dialogue
    Model: eleven_v3 (REQUIRED — only model that supports this endpoint)
    Inputs: list of {text, voice_id} pairs
    Returns: path to the saved mp3 file

    From ElevenLabs docs:
    - Max 2000 chars across all inputs[].text combined
    - Nondeterministic — use seed for reproducibility
    - No limit on number of speakers
    - Not for real-time use — batch generation only
    """
    inputs = []
    for line in script_lines:
        voice_id = author_voice_id if line["speaker"] == "AUTHOR" else reader_voice_id
        inputs.append({
            "text": line["text"],
            "voice_id": voice_id
        })

    print(f"  → Calling Text-to-Dialogue with {len(inputs)} lines...")
    print(f"     Total chars: {sum(len(i['text']) for i in inputs)}")

    # Generate 2 takes, we'll use the first (or let user re-trigger for variety)
    audio_generator = client.text_to_dialogue.convert(
        inputs=inputs,
        model_id="eleven_v3",           # REQUIRED — only v3 supports Text-to-Dialogue
        output_format="mp3_44100_128",  # high quality mp3
        seed=42                          # for reproducibility (from docs FAQ)
    )

    # Collect bytes from generator
    audio_bytes = b""
    for chunk in audio_generator:
        if chunk:
            audio_bytes += chunk

    if not audio_bytes:
        raise RuntimeError("Text-to-Dialogue returned empty audio")

    out_path = tempfile.mktemp(suffix="_dialogue.mp3", prefix="echoes_")
    Path(out_path).write_bytes(audio_bytes)
    print(f"  → Dialogue saved: {out_path} ({len(audio_bytes):,} bytes)")
    return out_path


# ─── Step 5: Background music ─────────────────────────────────────────────────

def generate_background_score() -> str:
    """
    Generates a background music track using ElevenLabs Music API.
    Low tension, ambient office atmosphere — sits underneath the dialogue.
    """
    print("  → Generating background score with Music API...")

    try:
        music_generator = client.music.compose(
            prompt="low tension ambient office atmosphere, subtle drone, cinematic tension, minimal, 60 seconds, dark ambient"
        )

        music_bytes = b""
        for chunk in music_generator:
            if chunk:
                music_bytes += chunk

        if not music_bytes:
            print("  WARNING: Music API returned empty audio — skipping background score")
            return None

        out_path = tempfile.mktemp(suffix="_music.mp3", prefix="echoes_")
        Path(out_path).write_bytes(music_bytes)
        print(f"  → Music saved: {out_path} ({len(music_bytes):,} bytes)")
        return out_path

    except Exception as e:
        print(f"  WARNING: Music API failed ({e}) — proceeding without background score")
        return None


# ─── Step 6: Mix audio with ffmpeg ────────────────────────────────────────────

def mix_and_export(dialogue_path: str, music_path: str | None) -> str:
    """
    Mixes dialogue (100%) with background score (12%) using ffmpeg.
    If no music, just copies the dialogue file.
    Returns path to the final mp3.
    """
    out_path = tempfile.mktemp(suffix="_final.mp3", prefix="echoes_")

    if not music_path:
        # No music — just use dialogue as-is
        subprocess.run(["cp", dialogue_path, out_path], check=True)
        return out_path

    print("  → Mixing dialogue + background score with ffmpeg...")

    # Check ffmpeg is available
    try:
        subprocess.run(["ffmpeg", "-version"],
                      capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("  WARNING: ffmpeg not found — outputting dialogue only")
        subprocess.run(["cp", dialogue_path, out_path], check=True)
        return out_path

    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", dialogue_path,
        "-i", music_path,
        "-filter_complex",
        # Dialogue at 100%, background score at 12% volume
        "[1:a]volume=0.12[bg];[0:a][bg]amix=inputs=2:duration=first:normalize=0[out]",
        "-map", "[out]",
        "-codec:a", "libmp3lame",
        "-b:a", "128k",
        out_path
    ], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  WARNING: ffmpeg mix failed — outputting dialogue only")
        print(f"  ffmpeg stderr: {result.stderr[-500:]}")
        subprocess.run(["cp", dialogue_path, out_path], check=True)

    return out_path


# ─── Step 7: Play audio ────────────────────────────────────────────────────────

def play_audio(filepath: str) -> None:
    """
    Plays audio through speakers using mpv (cross-platform).
    Falls back to afplay (macOS), aplay (Linux), or opens the file.
    """
    players = [
        ["mpv", "--no-video", "--really-quiet", filepath],
        ["afplay", filepath],          # macOS fallback
        ["aplay", filepath],           # Linux fallback
    ]

    for cmd in players:
        try:
            subprocess.run(["which", cmd[0]], capture_output=True, check=True)
            print(f"  → Playing with {cmd[0]}...")
            subprocess.Popen(cmd)
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue

    # Last resort — open with system default
    try:
        os.startfile(filepath)  # Windows
    except AttributeError:
        subprocess.Popen(["open", filepath])  # macOS
    print(f"  → Opened with system player: {filepath}")


# ─── Step 8: Print the drama script to stdout ─────────────────────────────────

def print_script(script: dict, filepath: str) -> None:
    """Prints the generated script to Kiro's agent context (via stdout)."""
    print("\n" + "─" * 60)
    print(f"ECHOES — {Path(filepath).name}")
    print("─" * 60)
    print()
    for line in script.get("lines", []):
        speaker = line["speaker"]
        text = line["text"]
        label = "AUTHOR →" if speaker == "AUTHOR" else "READER →"
        print(f"  {label}  {text}")
    print()
    print("─" * 60)
    total_chars = sum(len(l["text"]) for l in script.get("lines", []))
    print(f"Script: {len(script.get('lines', []))} lines, {total_chars} chars")
    print("─" * 60 + "\n")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/echoes.py <filepath>")
        print("       The file must be tracked by git.")
        sys.exit(1)

    filepath = sys.argv[1]

    # Validate file exists
    if not os.path.exists(filepath):
        print(f"ERROR: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    print(f"\n🎭 ECHOES — {Path(filepath).name}")
    print("─" * 50)

    # ── Step 1: Git history ──────────────────────────────
    print("\n[1/6] Parsing git history...")
    commits = get_git_history(filepath)

    if not commits:
        print(f"\n  No git history found for: {filepath}")
        print("  Make sure this file is tracked by git (git add + git commit).")
        sys.exit(0)

    unique_authors = list(dict.fromkeys([c['author'] for c in commits]))
    print(f"  → {len(commits)} commits by {len(unique_authors)} author(s):")
    for author in unique_authors[:5]:
        count = sum(1 for c in commits if c['author'] == author)
        print(f"     {author} ({count} commits)")

    # ── Step 2: Drama script ─────────────────────────────
    print("\n[2/6] Writing drama script (Gemini 2.5 Flash)...")
    try:
        script = generate_drama_script(filepath, commits)
    except Exception as e:
        print(f"ERROR: Failed to generate script: {e}", file=sys.stderr)
        sys.exit(1)

    print_script(script, filepath)

    # ── Step 3: Voice selection ──────────────────────────
    print("[3/6] Selecting voices...")

    # Try Voice Design for unique per-author voices
    # Falls back to library voices if API fails or quota exceeded
    author_seed = hashlib.md5(
        unique_authors[0].encode() if unique_authors else b"author"
    ).hexdigest()[:8]
    reader_seed  = hashlib.md5(
        (unique_authors[1] if len(unique_authors) > 1 else "reader").encode()
    ).hexdigest()[:8]

    author_persona = script.get("author_persona", "a gruff, experienced software engineer who sounds slightly defensive")
    reader_persona = script.get("reader_persona", "a curious, methodical software engineer who sounds genuinely puzzled")

    author_voice = get_or_create_voice(author_persona, f"author_{author_seed}", VOICE_AUTHOR)
    reader_voice = get_or_create_voice(reader_persona, f"reader_{reader_seed}", VOICE_READER)

    print(f"  → AUTHOR voice: {author_voice}")
    print(f"  → READER voice: {reader_voice}")

    # ── Step 4: Text-to-Dialogue ─────────────────────────
    print("\n[4/6] Generating dialogue (ElevenLabs Text-to-Dialogue, eleven_v3)...")
    try:
        dialogue_path = generate_dialogue(script["lines"], author_voice, reader_voice)
    except Exception as e:
        print(f"ERROR: Text-to-Dialogue failed: {e}", file=sys.stderr)
        print("  Tip: Check your ELEVENLABS_API_KEY and account credits.", file=sys.stderr)
        sys.exit(1)

    # ── Step 5: Background score ─────────────────────────
    print("\n[5/6] Generating background score (ElevenLabs Music API)...")
    music_path = generate_background_score()

    # ── Step 6: Mix + play ───────────────────────────────
    print("\n[6/6] Mixing and playing...")
    try:
        final_path = mix_and_export(dialogue_path, music_path)
    except Exception as e:
        print(f"WARNING: Mix failed ({e}) — playing dialogue only")
        final_path = dialogue_path

    play_audio(final_path)

    print(f"\n✅  ECHOES done → {final_path}")
    print("    Re-trigger the hook for a different take (nondeterministic model).\n")

    # Clean up temp files (keep final)
    for tmp in [dialogue_path, music_path]:
        if tmp and tmp != final_path and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass

    # Return the final path in stdout so Kiro agent can reference it
    return final_path


if __name__ == "__main__":
    main()
