# Echoes — Git History as Radio Drama

> Trigger it on any file. Hear the original engineers argue about why they wrote it that way.

[![Install in Kiro](https://img.shields.io/badge/Install%20in%20Kiro-Power-green?style=flat-square)](https://kiro.dev/launch/powers/echoes)

## What it does

Echoes is a Kiro Power that turns your git history into a voiced radio drama.

1. Open any file in Kiro
2. Trigger the "Echoes" hook from the Agent Hooks panel
3. Hear two AI voices — the original engineer and the current reader — arguing about the code

Powered by ElevenLabs Text-to-Dialogue (Eleven v3), Voice Design, and the Music API.

## Install

**Via Kiro Powers panel:**
1. Open Kiro → Powers panel
2. Click "Add power from GitHub"
3. Enter this repository URL

**Or from the command line:**
```bash
git clone https://github.com/YOUR_USERNAME/echoes-power
cd echoes-power
pip install -r requirements.txt
cp .env.example .env
# Fill in your API keys in .env
```

## Usage

Open any git-tracked file in Kiro → Hooks panel → click play on "Echoes — Hear the history of this file".

Or run directly:
```bash
python scripts/echoes.py src/auth.py
```

## Requirements

- Python 3.10+
- `git` (installed and in PATH)
- `ffmpeg` (`brew install ffmpeg`)
- `mpv` (`brew install mpv`)
- ElevenLabs API key (paid plan for Text-to-Dialogue)
- OpenAI API key

## ElevenLabs APIs used

| API | Purpose |
|-----|---------|
| Text-to-Dialogue (`/v1/text-to-dialogue`) | Multi-speaker voiced drama, Eleven v3 model |
| Voice Design | Unique synthetic voice per author |
| Music API | Background ambient score |

## Built for ElevenHacks Hack #5 × Kiro

Built in 2 days for ElevenHacks — the ElevenLabs weekly hackathon series.
