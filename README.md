# WildWhisper

WildWhisper is a hands-free trail naturalist assistant. Give it a trail photo
and a question, then get a concise spoken answer from a local vision model
through Ollama. Ask by typing or, optionally, dictate your question with local
Vosk speech recognition.

## Features

- Analyzes a photo and question with a vision-capable Ollama model. The default
  model is `llava`.
- Accepts typed questions or optional offline voice input with Vosk.
- Speaks answers using ElevenLabs when configured, with a local text-to-speech
  fallback.
- Saves ElevenLabs audio as `trail_response.mp3` and plays it with the host
  operating system's audio player.
- Optionally records application tracing and errors with Sentry.

Image analysis and optional voice transcription run locally. If enabled,
ElevenLabs receives the generated answer text for speech synthesis, and Sentry
receives telemetry. Omit `ELEVENLABS_API_KEY` and `SENTRY_DSN` to avoid those
optional online services.

## Project files

| Path | Description |
| --- | --- |
| `app.py` | Command-line application and audio/inference pipeline |
| `requirements.txt` | Core Python dependencies |
| `sample/trail.jpg` | Example photo for trying the application |
| `.gitignore` | Ignores virtual environments, caches, and generated audio |
| `LICENSE` | MIT License |

## Requirements

- Python 3.10 or later
- [Ollama](https://ollama.com/) with a vision-capable model
- A microphone and the optional Vosk model for voice input
- Optional: ElevenLabs API key for online audio synthesis
- Optional: Sentry DSN for remote tracing and error reporting
- For local speech fallback:
  - macOS: built-in `say`
  - Windows: Windows Speech via PowerShell
  - Linux: `espeak-ng` or `espeak`
- For ElevenLabs MP3 playback:
  - macOS: `afplay`
  - Windows: the default application for MP3 files
  - Linux: `ffmpeg` and `aplay`, or `mpg123` or `ffplay`

## Installation

### 1. Install Ollama and a vision model

Install and start Ollama, then download the default model:

```bash
ollama pull llava
```

To use another vision-capable Ollama model, set `OLLAMA_MODEL` as described
under [Configuration](#configuration).

### 2. Set up Python

Create and activate a virtual environment from the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

On macOS or Linux, activate it with:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the application dependencies:

```bash
python -m pip install -r requirements.txt
```

### 3. (Optional) Enable voice input

Install Vosk:

```bash
python -m pip install vosk
```

Download and unpack a small English Vosk model into
`models/vosk-model-small-en-us-0.15`. The model is available from the
[Vosk model downloads](https://alphacephei.com/vosk/models) page. To use a
different unpacked model, set `VOSK_MODEL_PATH`. The microphone and
`sounddevice` are also required.

### 4. (Optional) Configure services

Without `ELEVENLABS_API_KEY`, answers are spoken locally instead of being sent
to ElevenLabs. Set the key to enable MP3 synthesis. Set `SENTRY_DSN` to enable
optional tracing and error reporting.

```powershell
# Windows PowerShell
$env:ELEVENLABS_API_KEY = "your-elevenlabs-api-key"
$env:SENTRY_DSN = "https://your-sentry-dsn"
```

```bash
# macOS / Linux
export ELEVENLABS_API_KEY="your-elevenlabs-api-key"
export SENTRY_DSN="https://your-sentry-dsn"
```

Sentry is disabled when no DSN is configured. The application disables Sentry's
default personally identifiable data collection.

## Run

Make sure Ollama is running and ask about the included sample photo:

```bash
python app.py sample/trail.jpg "What animal is in this photo?"
```

For local voice dictation (listens for up to eight seconds):

```bash
python app.py sample/trail.jpg --listen
```

Pass either a question or `--listen`, not both. Answers are requested in no
more than two short sentences. If local inference fails, the app reports the
problem and tries to speak a fallback message. If ElevenLabs synthesis or
playback fails, it falls back to local speech or prints the answer.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_URL` | `http://localhost:11434` | Local Ollama base URL |
| `OLLAMA_MODEL` | `llava` | Vision-capable Ollama model name |
| `ELEVENLABS_API_KEY` | Unset | Enables ElevenLabs MP3 synthesis |
| `ELEVENLABS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | ElevenLabs voice |
| `VOSK_MODEL_PATH` | `models/vosk-model-small-en-us-0.15` | Unpacked offline transcription model |
| `SENTRY_DSN` | Unset | Enables Sentry tracing and error reporting |
