# WildWhisper

WildWhisper is a hands-free trail naturalist assistant. Give it a trail photo
and a question, then get a concise spoken answer from a local vision model
through Ollama. Ask by typing or, optionally, dictate your question with local
Vosk speech recognition.

## How it works

Photo → Moondream → concise answer → ElevenLabs → audio playback

Vosk provides optional local voice input. If ElevenLabs is not configured or
unavailable, WildWhisper uses the local speech fallback.

## Features

- Analyzes a photo and question with a vision-capable Ollama model. The default
  model is `moondream`.
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

## Offline / Local-first

Ollama/Moondream image analysis and Vosk voice transcription run locally.
WildWhisper also has an offline speech fallback. ElevenLabs requires internet
access for speech generation, and Sentry requires internet access when enabled
to send telemetry. The application is local-first and offline-capable, but it
is not completely offline when either cloud service is used.

## Project files

| Path | Description |
| --- | --- |
| `app.py` | Command-line application and audio/inference pipeline |
| `requirements.txt` | Core Python dependencies |
| `sample/trail.jpg` | Example photo for trying the application |
| `.gitignore` | Ignores virtual environments, caches, generated audio, and local models |
| `LICENSE` | MIT License |

The `models/` directory is for locally downloaded models and is ignored by Git;
it is not a committed project directory.

## Requirements

- Python 3.10 or later
- [Ollama](https://ollama.com/) with the Moondream model available locally
- A working microphone and the Vosk model for voice input
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

### 1. Install Ollama and Moondream

Install Ollama and make sure it is running locally. Download the default
Moondream model:

```bash
ollama pull moondream
```

Before running WildWhisper, ensure the local Ollama service is running and the
Moondream model is available. The default Ollama URL is
`http://localhost:11434`. To use another vision-capable Ollama model, set
`OLLAMA_MODEL` as described under [Configuration](#configuration).

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

The Vosk package is installed with the dependencies in `requirements.txt`.
Download a small English Vosk model from the
[Vosk model downloads](https://alphacephei.com/vosk/models) page and extract it
into `models/vosk-model-small-en-us-0.15/`. The `models/` directory is ignored
by Git because it contains locally downloaded models. To use a different
unpacked model, set `VOSK_MODEL_PATH`. Voice input also requires a working
microphone.

### 4. (Optional) Configure ElevenLabs and Sentry

Create a `.env` file manually in the project root for optional service
configuration:

```dotenv
SENTRY_DSN=YOUR_SENTRY_DSN
ELEVENLABS_API_KEY=YOUR_ELEVENLABS_API_KEY
ELEVENLABS_VOICE_ID=YOUR_ELEVENLABS_VOICE_ID
```

The application loads `.env` at startup. Never commit `.env` to GitHub; keep
it private and do not share real keys or DSNs. Without an ElevenLabs API key,
answers use the local speech fallback.

ElevenLabs is used to generate speech from WildWhisper's answer. Configure
`ELEVENLABS_API_KEY` with your API key and `ELEVENLABS_VOICE_ID` with the voice
to use. This service requires internet access.

When `SENTRY_DSN` is configured, Sentry is used for error monitoring and
performance tracing. `traces_sample_rate=1.0` means all transactions are
sampled. `send_default_pii=False` disables default PII collection, but
telemetry is still sent to Sentry when enabled.

## Run

Make sure Ollama is running locally and the Moondream model is available, then
ask about the included sample photo:

```bash
python app.py sample/trail.jpg "What can you tell me about this plant?"
```

For local voice dictation (requires a working microphone and listens for up to
eight seconds):

```bash
python app.py sample/trail.jpg --listen
```

Pass either a question or `--listen`, not both. Answers are requested in no
more than two short sentences. If local inference fails, the app reports the
problem and tries to speak a fallback message. If ElevenLabs synthesis or
playback fails, it falls back to local speech or prints the answer.

## Limitations

- Vision inference speed depends on your hardware.
- Species identification from a single image may be uncertain.
- Safety answers are not definitive expert identification or medical advice.
- ElevenLabs speech generation requires network access.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_URL` | `http://localhost:11434` | Local Ollama base URL |
| `OLLAMA_MODEL` | `moondream` | Vision-capable Ollama model name |
| `ELEVENLABS_API_KEY` | Unset | Enables ElevenLabs MP3 synthesis |
| `ELEVENLABS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | ElevenLabs voice |
| `VOSK_MODEL_PATH` | `models/vosk-model-small-en-us-0.15` | Unpacked offline transcription model |
| `SENTRY_DSN` | Unset | Enables Sentry tracing and error reporting |
