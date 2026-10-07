# Hands-Free Trail Naturalist

A lightweight, voice-first naturalist assistant for the Hacktoberfest 2026
**Touch Grass** challenge. Give it a trail photo and a question; local Gemma
inference describes what it sees, and the answer is spoken aloud. Use typed
questions or optional local voice dictation, so you can keep your attention on
the trail rather than a screen.

## How it works

1. Optional voice input is recorded from the microphone and transcribed locally
   by Vosk. Text questions can be passed directly on the command line.
2. The photo and question are sent to the local Ollama `gemma2:2b` model.
3. When an ElevenLabs API key is configured, its SDK synthesizes an MP3 response
   saved as `trail_response.mp3`. Without a key, the app uses the system's
   offline speech engine instead.
4.    MP3 playback uses `afplay` on macOS and `mpg123`/`ffplay` on Linux (or `ffmpeg`
   piped to `aplay`). Local fallback speech uses macOS `say`, Windows Speech, or
   Linux `espeak-ng`/`espeak`.
5. Sentry SDK agent tracing records pipeline timing when `SENTRY_DSN` is set.

Gemma, Vosk, and local system speech keep core recognition, inference, and
fallback narration on-device. ElevenLabs synthesis and Sentry reporting are
optional online services. For the most offline use, omit the ElevenLabs API key
and Sentry DSN.

## Requirements

- Python 3.10 or later
- [Ollama](https://ollama.com/) with `gemma2:2b`
- Microphone and a downloaded Vosk model for `--listen` voice input
- `afplay` (macOS), `mpg123` or `ffplay` (Linux) for ElevenLabs MP3 playback;
  alternatively, Linux can use `ffmpeg` piped to `aplay`
- Linux local voice fallback: `espeak-ng` or `espeak`
- Optional: ElevenLabs API key for MP3 synthesis
- Optional: Sentry DSN for remote tracing

## Install

### 1. Install Ollama and pull Gemma

Install Ollama from [ollama.com](https://ollama.com/), start it, then pull the
local vision model:

```bash
ollama pull gemma2:2b
```

### 2. Install Python packages

From the project directory:

```bash
python -m pip install -r requirements.txt
```

### 3. (Optional) Set up offline voice input

Download and unpack the small English Vosk model into
`models/vosk-model-small-en-us-0.15`. The model is available from the
[Vosk model downloads](https://alphacephei.com/vosk/models) page. Alternatively,
set `VOSK_MODEL_PATH` to the path of another compatible unpacked model.

### 4. (Optional) Configure ElevenLabs and Sentry

Without `ELEVENLABS_API_KEY`, speech is spoken locally rather than sent to
ElevenLabs. To enable ElevenLabs MP3 synthesis, set the key:

```bash
# macOS / Linux
export ELEVENLABS_API_KEY="your-elevenlabs-api-key"
export SENTRY_DSN="https://your-sentry-dsn" # optional
```

```powershell
# Windows PowerShell
$env:ELEVENLABS_API_KEY = "your-elevenlabs-api-key"
$env:SENTRY_DSN = "https://your-sentry-dsn" # optional
```

Sentry tracing is enabled only when a DSN is configured. The application avoids
sending default personally identifiable data.

## Run

Ensure Ollama is running, then provide a photo and a typed question:

```bash
python app.py sample_photos/trail.jpg "Is this plant safe to touch?"
```

To dictate the question instead (Vosk listens for up to eight seconds):

```bash
python app.py sample_photos/trail.jpg --listen
```

The Gemma response is requested in two concise sentences. Local inference
failures trigger a polite spoken message using the system speech engine. If
ElevenLabs is unavailable, a successful Gemma answer is spoken locally instead.
On macOS the local `say` command is built in; on Linux, install `espeak-ng` or
`espeak` for offline narration.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_URL` | `http://localhost:11434` | Local Ollama base URL |
| `OLLAMA_MODEL` | `gemma2:2b` | Ollama model name |
| `ELEVENLABS_API_KEY` | Unset | Enables online ElevenLabs MP3 synthesis |
| `ELEVENLABS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | ElevenLabs voice |
| `VOSK_MODEL_PATH` | `models/vosk-model-small-en-us-0.15` | Unpacked offline transcription model |
| `SENTRY_DSN` | Unset | Enables Sentry tracing and error reporting |
