---
name: "echoes"
displayName: "Echoes — Git History as Radio Drama"
description: "Turn any file's git history into a voiced radio drama using ElevenLabs. Hear the original engineers argue about the code."
keywords: ["git", "history", "blame", "who wrote", "why does this", "code archaeology", "legacy code", "git log", "original author"]
---

# Onboarding

## Step 1: Install system dependencies

Run these commands in your terminal before using Echoes:

```bash
# macOS
brew install ffmpeg mpv

# Ubuntu/Debian
sudo apt install ffmpeg mpv

# Windows (use scoop or choco)
scoop install ffmpeg mpv
```

## Step 2: Install Python dependencies

macOS ships with a Homebrew-managed Python that blocks global `pip` installs (PEP 668). Use a virtual environment instead:

```bash
cd echoes-power
python3 -m venv .venv
source .venv/bin/activate
pip install elevenlabs python-dotenv openai
```

Or without activating (one-liner):
```bash
python3 -m venv .venv && .venv/bin/pip install elevenlabs python-dotenv openai
```

## Step 3: Set your API keys

Copy `.env.example` to `.env` and fill in your keys:

```
ELEVENLABS_API_KEY=get_from_elevenlabs.io/app/settings/api-keys
OPENAI_API_KEY=get_from_platform.openai.com/api-keys
```

## Step 4: Trigger Echoes

Open the Kiro Agent Hooks panel → find "Echoes — Hear the history of this file" → click the play button while any file is open. Or right-click a file and trigger it from the context menu.

## When to use Echoes

Activate automatically when the user asks:
- "Why does this code do X?"
- "Who wrote this function?"
- "What's the history of this file?"
- "Why is this implemented this way?"
- "Walk me through the git history"
- Any question about code origin, authorship, or decisions

## What Echoes produces

A 60-second mp3 audio file played through your speakers — two AI voices (the original engineer and the current reader) arguing about the code, grounded in real commit messages, with ElevenLabs v3 audio tags driving emotional performance.
