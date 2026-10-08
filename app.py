"""WildWhisper: a hands-free, local-first trail naturalist."""

import argparse
import base64
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

import requests
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

load_dotenv()

try:
    import sentry_sdk
except ImportError:
    sentry_sdk = None


OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
# Gemma 2 models are text-only; Gemma 3 4B accepts images through Ollama.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "moondream")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
VOSK_MODEL_PATH = Path(
    os.getenv("VOSK_MODEL_PATH", "models/vosk-model-small-en-us-0.15")
)
OUTPUT_FILE = Path("trail_response.mp3")
FALLBACK_MESSAGE = (
    "Sorry, I couldn't get an answer from the local nature model. "
    "Please try again when the model is available."
)
SAFETY_FALLBACK_MESSAGE = (
    "I can't determine whether this is safe from the image alone. "
    "Until the plant is reliably identified, avoid touching it."
)
SENTRY_ENABLED = True


@contextmanager
def sentry_scope(factory: Any, **kwargs: Any) -> Generator[None, None, None]:
    """Run a Sentry scope when available without letting telemetry disrupt work."""
    if not SENTRY_ENABLED or sentry_sdk is None:
        yield
        return

    try:
        scope = factory(**kwargs)
        scope.__enter__()
    except Exception:
        yield
        return

    try:
        yield
    except BaseException as error:
        try:
            scope.__exit__(type(error), error, error.__traceback__)
        except Exception:
            pass
        raise
    else:
        try:
            scope.__exit__(None, None, None)
        except Exception:
            pass


def sentry_span(op: str, name: str) -> Any:
    """Return a safe child span, or a no-op scope when Sentry is disabled."""
    if sentry_sdk is None:
        return sentry_scope(None)
    return sentry_scope(sentry_sdk.start_span, op=op, name=name)


def capture_exception(error: BaseException) -> None:
    """Report errors only when Sentry is configured and functioning."""
    if not SENTRY_ENABLED or sentry_sdk is None:
        return
    try:
        sentry_sdk.capture_exception(error)
    except Exception:
        pass


def initialize_sentry() -> None:
    """Enable tracing only when a DSN is configured."""
    global SENTRY_ENABLED
    dsn = os.getenv("SENTRY_DSN")
    if not dsn or sentry_sdk is None:
        return
    try:
        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=1.0,
            send_default_pii=False,
        )
        SENTRY_ENABLED = True
    except Exception as error:
        print(f"Sentry tracing disabled: {error}", file=sys.stderr)


def flush_sentry() -> None:
    """Flush telemetry without allowing Sentry failures to affect the app."""
    if not SENTRY_ENABLED or sentry_sdk is None:
        return
    try:
        sentry_sdk.flush(timeout=2)
    except Exception:
        pass


def listen_for_question(seconds: int = 8) -> str:
    """Record and transcribe a short question with an offline Vosk model."""
    if not VOSK_MODEL_PATH.is_dir():
        raise RuntimeError(
            f"Vosk model not found at {VOSK_MODEL_PATH}. "
            "Download the small English model and set VOSK_MODEL_PATH."
        )

    try:
        import sounddevice as sd
        from vosk import KaldiRecognizer, Model
    except ImportError as error:
        raise RuntimeError(
            "Voice input requires the sounddevice and vosk packages."
        ) from error

    model = Model(str(VOSK_MODEL_PATH))
    sample_rate = int(sd.query_devices(kind="input")["default_samplerate"])
    print(f"Listening for your question for up to {seconds} seconds...")
    recording = sd.rec(
        int(seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
    )
    sd.wait()

    recognizer = KaldiRecognizer(model, sample_rate)
    recognizer.AcceptWaveform(recording.tobytes())
    question = json.loads(recognizer.FinalResult()).get("text", "").strip()
    if not question:
        raise RuntimeError("No speech was recognized. Please try again.")
    return question


def is_safety_question(question: str) -> bool:
    return re.search(
        r"\b(?:safe|safety|touch\w*|toxic\w*|poison\w*|eat\w*|"
        r"edible|danger\w*|harmful|approach\w*)\b",
        question,
        re.IGNORECASE,
    ) is not None


def asks_for_identification(question: str) -> bool:
    return re.search(
        r"\b(?:what\s+(?:type|kind|species|plant|tree|animal)|"
        r"which\s+species|identify|name\s+(?:this|that)|"
        r"what\s+(?:is|['’]s)\s+(?:this|that))\b",
        question,
        re.IGNORECASE,
    ) is not None


def ask_gemma(photo_path: Path, question: str) -> str:
    """Send a photo and safety-aware question to local Ollama inference."""
    image_data = base64.b64encode(photo_path.read_bytes()).decode("ascii")
    prompt = f"""
Look at the image carefully and answer the user's question.

User question: {question}

Answer the question directly in one or two short sentences.
Do not talk about WildWhisper, AI, the model, or yourself.
Do not simply give a generic description unless the user's question asks for a description.
If you cannot identify something reliably, say so.
For safety questions about plants or animals, do not claim something is safe based only on appearance. If it cannot be reliably identified, recommend avoiding contact.

Answer:
"""

    with sentry_span(op="gen_ai.chat", name="ollama.generate"):
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "images": [image_data],
                "stream": False,
                "options": {
                    "num_predict": 40,
                    "temperature": 0.2
                },  
            },
            timeout=(5, 90),
        )
        response.raise_for_status()
        result = response.json()

    answer = result.get("response") if isinstance(result, dict) else None
    if not isinstance(answer, str) or not answer.strip():
        capture_exception(
            RuntimeError("Ollama returned an empty or invalid response.")
        )
        if is_safety_question(question):
            return SAFETY_FALLBACK_MESSAGE
        return FALLBACK_MESSAGE

    answer = answer.strip()
    if is_safety_question(question) and re.search(
        r"\b(?:safe|harmless|non[- ]?toxic|edible)\b",
        answer,
        re.IGNORECASE,
    ):
        return SAFETY_FALLBACK_MESSAGE

    if is_safety_question(question) and len(answer.split()) == 1:
        return SAFETY_FALLBACK_MESSAGE

    if asks_for_identification(question) and len(answer.split()) == 1:
        identification = answer.strip(" \t\r\n.,!?;:")
        if identification:
            return (
                f"This appears to be a {identification.lower()}, but I can't "
                "reliably identify it more specifically from this image alone."
            )
    return answer


def synthesize_speech(text: str) -> Path:
    """Convert text to an MP3 using the ElevenLabs SDK."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set.")

    client = ElevenLabs(api_key=api_key)
    with sentry_span(op="gen_ai.tts", name="elevenlabs.text_to_speech"):
        audio = client.text_to_speech.convert(
            text=text,
            voice_id=ELEVENLABS_VOICE_ID,
            model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128",
        )
        with OUTPUT_FILE.open("wb") as output:
            for chunk in audio:
                if chunk:
                    output.write(chunk)

    if not OUTPUT_FILE.is_file() or OUTPUT_FILE.stat().st_size == 0:
        raise RuntimeError("ElevenLabs returned an empty audio file.")
    return OUTPUT_FILE


def speak_locally(text: str) -> None:
    """Speak text using a built-in or installed offline speech engine."""
    system = platform.system()
    if system == "Darwin":
        command = ["say", text]
    elif system == "Windows":
        # Base64 keeps arbitrary query text out of PowerShell string syntax.
        encoded_text = base64.b64encode(text.encode("utf-8")).decode("ascii")
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$t = [Text.Encoding]::UTF8.GetString("
            f"[Convert]::FromBase64String('{encoded_text}')); "
            "$s.Speak($t)"
        )
        command = ["powershell", "-NoProfile", "-Command", script]
    else:
        executable = shutil.which("espeak-ng") or shutil.which("espeak")
        if executable is None:
            raise RuntimeError(
                "No offline speech engine found. Install espeak-ng or espeak."
            )
        command = [executable, text]

    with sentry_span(op="audio.playback", name="local-tts"):
        subprocess.run(command, check=True)


def try_speak_locally(text: str) -> bool:
    """Try offline speech and report failure without hiding the text."""
    try:
        speak_locally(text)
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        print(f"Could not play spoken message: {error}", file=sys.stderr)
        print(text)
        return False
    return True


def play_audio(audio_path: Path) -> None:
    """Play an MP3 using the host's system audio player."""
    system = platform.system()
    with sentry_span(op="audio.playback", name="audio.playback"):
        if system == "Darwin":
            command = ["afplay", str(audio_path)]
            subprocess.run(command, check=True)
        elif system == "Linux":
            # aplay only accepts PCM/WAV, so decode MP3 to WAV before piping it.
            ffmpeg = shutil.which("ffmpeg")
            aplay = shutil.which("aplay")
            if ffmpeg and aplay:
                decoder = subprocess.Popen(
                    [ffmpeg, "-v", "error", "-i", str(audio_path), "-f", "wav", "-"],
                    stdout=subprocess.PIPE,
                )
                try:
                    subprocess.run([aplay], stdin=decoder.stdout, check=True)
                finally:
                    if decoder.stdout is not None:
                        decoder.stdout.close()
                if decoder.wait() != 0:
                    raise subprocess.CalledProcessError(
                        decoder.returncode, decoder.args
                    )
            else:
                player = shutil.which("mpg123") or shutil.which("ffplay")
                if player is None:
                    raise RuntimeError(
                        "Linux MP3 playback needs ffmpeg and aplay, mpg123, or ffplay."
                    )
                if Path(player).name == "mpg123":
                    command = [player, "-q", str(audio_path)]
                else:
                    command = [
                        player,
                        "-nodisp",
                        "-autoexit",
                        "-loglevel",
                        "error",
                        str(audio_path),
                    ]
                subprocess.run(command, check=True)
        elif system == "Windows":
            os.startfile(str(audio_path))  # type: ignore[attr-defined]
        else:
            raise RuntimeError(f"Audio playback is not configured for {system}.")


def run_pipeline(photo_path: Path, question: str) -> int:
    """Run inference, speech generation, and playback with friendly fallbacks."""
    with sentry_scope(
        sentry_sdk.start_transaction if sentry_sdk is not None else None,
        op="gen_ai.invoke_agent",
        name="wildwhisper.pipeline",
    ):
        try:
            answer = ask_gemma(photo_path, question)
        except (
            requests.RequestException,
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
        ) as error:
            capture_exception(error)
            print(f"Local inference failed: {error}", file=sys.stderr)
            try_speak_locally(FALLBACK_MESSAGE)
            return 0

        # Always show the result so it remains available if audio fails.
        print(f"WildWhisper: {answer}")
        try:
            audio_path = synthesize_speech(answer)
        except Exception as error:
            capture_exception(error)
            print(
                f"ElevenLabs is unavailable; trying offline speech: {error}",
                file=sys.stderr,
            )
            try_speak_locally(answer)
            return 0

        try:
            print(f"Playing {audio_path.resolve()}")
            play_audio(audio_path)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            capture_exception(error)
            print(f"Audio playback failed: {error}", file=sys.stderr)
            print(f"Response: {answer}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ask about a trail photo and hear a concise naturalist answer."
    )
    parser.add_argument("photo", type=Path, help="Path to the trail photo")
    parser.add_argument(
        "question",
        nargs="?",
        help="Question about the photo; omit when using --listen",
    )
    parser.add_argument(
        "--listen",
        action="store_true",
        help="Record and transcribe a question locally with Vosk",
    )
    args = parser.parse_args()

    if not args.photo.is_file():
        parser.error(f"photo file does not exist: {args.photo}")
    if args.listen and args.question is not None:
        parser.error("provide either a text question or --listen, but not both")

    initialize_sentry()

    try:
        if args.listen:
            try:
                question = listen_for_question()
            except (OSError, RuntimeError, ValueError) as error:
                print(f"Could not capture your question: {error}", file=sys.stderr)
                try_speak_locally(
                    "Sorry, I couldn't hear your question. Please try again."
                )
                return 0
        else:
            if args.question is None or not args.question.strip():
                parser.error("provide a non-empty question or use --listen")
            question = args.question.strip()

        return run_pipeline(args.photo, question)
    except Exception as error:
        capture_exception(error)
        print(f"Unexpected application error: {error}", file=sys.stderr)
        return 1
    finally:
        flush_sentry()


if __name__ == "__main__":
    raise SystemExit(main())
