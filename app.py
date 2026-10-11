"""WildWhisper: a hands-free, local-first trail naturalist."""

import argparse
import base64
import json
import logging
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

LOGGER = logging.getLogger(__name__)

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
VOSK_SAMPLE_RATE = 16000
VOSK_LOW_CONFIDENCE_THRESHOLD = 0.5
OUTPUT_FILE = Path("trail_response.mp3")
FALLBACK_MESSAGE = (
    "Sorry, I couldn't get an answer from the local nature model. "
    "Please try again when the model is available."
)
SAFETY_FALLBACK_MESSAGE = (
    "I can't determine whether this is safe from the image alone. "
    "Until the plant is reliably identified, avoid touching or eating it."
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


def _prepare_vosk_audio(recording: Any, source_rate: int) -> bytes:
    """Convert microphone samples to mono, 16-bit PCM at the Vosk model rate."""
    import numpy as np

    audio = np.asarray(recording)
    if audio.dtype != np.dtype("int16") or audio.ndim not in (1, 2):
        raise RuntimeError(
            "The microphone returned an unsupported audio format. Check its configuration and try again."
        )
    if source_rate <= 0 or audio.shape[0] == 0:
        raise RuntimeError(
            "The microphone recording was incomplete. Please record your question again."
        )

    if audio.ndim == 2:
        if audio.shape[1] < 1:
            raise RuntimeError(
                "The microphone recording did not contain an audio channel. Please try again."
            )
        channels = audio.astype(np.float64)
        channel_levels = np.sqrt(np.mean(np.square(channels), axis=0))
        audio = channels[:, int(np.argmax(channel_levels))]
    else:
        audio = audio.astype(np.float64)

    clipped_samples = np.count_nonzero(np.abs(audio) >= 32760)
    if clipped_samples / audio.size > 0.01:
        raise RuntimeError(
            "The microphone audio is clipping. Lower the input level or move farther from the microphone, then try again."
        )

    audio -= np.mean(audio)
    if float(np.max(np.abs(audio))) < 256:
        raise RuntimeError(
            "The microphone input was too quiet. Check the input level, speak closer to the microphone, and try again."
        )
    if source_rate != VOSK_SAMPLE_RATE:
        ratio = VOSK_SAMPLE_RATE / source_rate
        cutoff = 0.475 * min(1.0, ratio)
        half_width = 48
        positions = np.arange(-half_width, half_width + 1, dtype=np.float64)
        low_pass = 2 * cutoff * np.sinc(2 * cutoff * positions)
        low_pass *= np.hamming(low_pass.size)
        low_pass /= np.sum(low_pass)
        filtered = np.convolve(audio, low_pass, mode="same")
        output_length = round(audio.size * ratio)
        output_positions = np.arange(output_length) / ratio
        audio = np.interp(
            output_positions,
            np.arange(filtered.size),
            filtered,
            left=0.0,
            right=0.0,
        )

    pcm = np.clip(np.rint(audio), -32768, 32767).astype("<i2", copy=False)
    if pcm.size == 0:
        raise RuntimeError(
            "The microphone recording was incomplete. Please record your question again."
        )
    return pcm.tobytes()


def listen_for_question_with_confidence(seconds: int = 8) -> tuple[str, bool]:
    """Return a Vosk transcript and whether any reported word is low-confidence."""
    if seconds <= 0:
        raise ValueError("The recording duration must be positive.")
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

    try:
        device = sd.query_devices(kind="input")
    except sd.PortAudioError as error:
        message = str(error).casefold()
        if "permission" in message or "access denied" in message:
            raise RuntimeError(
                "Microphone permission was denied. Allow microphone access in your operating-system settings and try again."
            ) from error
        raise RuntimeError(
            "No microphone input device is available. Connect a microphone and try again."
        ) from error

    if device["max_input_channels"] < 1:
        raise RuntimeError(
            "No microphone input device is available. Connect a microphone and try again."
        )
    source_rate = int(device["default_samplerate"])
    if source_rate <= 0:
        raise RuntimeError(
            "The microphone reported an invalid sample rate. Check its configuration."
        )
    channels = 1
    try:
        sd.check_input_settings(
            device=device["index"],
            channels=channels,
            dtype="int16",
            samplerate=source_rate,
        )
    except sd.PortAudioError as error:
        raise RuntimeError(
            "The microphone does not support the required audio format. Check its configuration."
        ) from error

    print(f"Listening for your question for up to {seconds} seconds...")
    try:
        recording = sd.rec(
            int(seconds * source_rate),
            samplerate=source_rate,
            channels=channels,
            dtype="int16",
        )
        sd.wait()
    except sd.PortAudioError as error:
        message = str(error).casefold()
        if "permission" in message or "access denied" in message:
            raise RuntimeError(
                "Microphone permission was denied. Allow microphone access in your operating-system settings and try again."
            ) from error
        raise RuntimeError(
            "The microphone could not record audio. Check its connection and system permissions."
        ) from error

    expected_frames = int(seconds * source_rate)
    if getattr(recording, "shape", (0,))[0] < expected_frames:
        raise RuntimeError(
            "The microphone recording was cut short. Please record your question again."
        )
    audio_bytes = _prepare_vosk_audio(recording, source_rate)

    try:
        model = Model(str(VOSK_MODEL_PATH))
        recognizer = KaldiRecognizer(model, VOSK_SAMPLE_RATE)
        recognizer.SetWords(True)
        recognizer.AcceptWaveform(audio_bytes)
        result = json.loads(recognizer.FinalResult())
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        raise RuntimeError(
            "Local speech recognition failed. Check the Vosk model and try again."
        ) from error

    question = result.get("text", "").strip() if isinstance(result, dict) else ""
    if not question:
        raise RuntimeError(
            "No speech was recognized. Speak clearly near the microphone and try again."
        )
    words = result.get("result", []) if isinstance(result, dict) else []
    low_confidence = any(
        isinstance(word, dict)
        and isinstance(word.get("conf"), (int, float))
        and word["conf"] < VOSK_LOW_CONFIDENCE_THRESHOLD
        for word in words
    )
    return question, low_confidence


def listen_for_question(seconds: int = 8) -> str:
    """Record and transcribe a short question with an offline Vosk model."""
    question, _ = listen_for_question_with_confidence(seconds)
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


def _remove_positive_safety_claims(answer: str) -> str:
    """Remove unsupported safety assurances while retaining visual observations."""
    claim_pattern = re.compile(
        r"\b(?:(?:is|are|looks?|seems?|appears?|may be|might be|could be|should be)\s+)?"
        r"(?:to be\s+)?(?:safe|harmless|non[- ]?toxic|edible)"
        r"(?:\s+to\s+(?:touch|eat|consume))?\b",
        re.IGNORECASE,
    )
    denial_pattern = re.compile(
        r"\b(?:not|never|cannot|can't|unable|uncertain|unsure|unknown|don't|do not)\b"
        r"(?:\s+\w+){0,5}\s*$",
        re.IGNORECASE,
    )

    cleaned_sentences = []
    for sentence in re.split(r"(?<=[.!?])\s+", answer):
        def remove_claim(match: re.Match[str]) -> str:
            prefix = re.split(
                r"\b(?:but|and|however)\b",
                sentence[:match.start()],
                flags=re.IGNORECASE,
            )[-1]
            return match.group(0) if denial_pattern.search(prefix) else ""

        cleaned = claim_pattern.sub(remove_claim, sentence)
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = re.sub(r"\s+([,.;!?])", r"\1", cleaned)
        cleaned = re.sub(
            r"\s*,?\s*\b(?:but|and|however)"
            r"(?:\s+(?:it|they|this|these|those))?\s*[.!?;,:]*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = cleaned.strip(" \t\r\n,;:-")
        if cleaned and not re.fullmatch(
            r"(?:it|this|that|they|these|those|the plant|the leaves)[.!?]*",
            cleaned,
            re.IGNORECASE,
        ):
            cleaned_sentences.append(cleaned)

    return " ".join(cleaned_sentences)


def ask_gemma(photo_path: Path, question: str) -> str:
    """Send a photo and safety-aware question to local Ollama inference."""
    image_data = base64.b64encode(photo_path.read_bytes()).decode("ascii")
    prompt = f"""
Inspect the image carefully before answering the user's question.

User question: {question}

If leaves are visible, first describe their observable shape, color, edges, arrangement, and texture, but only when those details are clear in the image. Then give a tentative identification only if the visible evidence supports one. Clearly state uncertainty when the image does not support a reliable identification.
Answer the user's question directly in two or three short sentences. Describe only details that are visible.
Do not talk about WildWhisper, AI, the model, or yourself.
For safety questions, never say a plant or animal is safe, harmless, non-toxic, or edible based on appearance. Do not give a safety verdict; the application will add a caution.

Answer:
"""

    LOGGER.warning("Ollama diagnostic: request started (model=%s)", OLLAMA_MODEL)
    try:
        with sentry_span(op="gen_ai.chat", name="ollama.generate"):
            response = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "images": [image_data],
                    "stream": False,
                    "options": {
                        "num_predict": 100,
                        "temperature": 0.2
                    },
                },
                timeout=(5, 90),
            )
    except requests.RequestException as error:
        LOGGER.warning(
            "Ollama diagnostic: request failed (model=%s, error=%s)",
            OLLAMA_MODEL,
            type(error).__name__,
        )
        raise

    LOGGER.warning(
        "Ollama diagnostic: HTTP response received (model=%s, status=%s)",
        OLLAMA_MODEL,
        response.status_code,
    )
    try:
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as error:
        LOGGER.warning(
            "Ollama diagnostic: response could not be read (model=%s, status=%s, error=%s)",
            OLLAMA_MODEL,
            response.status_code,
            type(error).__name__,
        )
        raise

    answer = result.get("response") if isinstance(result, dict) else None
    has_response = isinstance(answer, str) and bool(answer.strip())
    LOGGER.warning(
        "Ollama diagnostic: response text available (model=%s, non_empty=%s)",
        OLLAMA_MODEL,
        has_response,
    )
    if not has_response:
        capture_exception(
            RuntimeError("Ollama returned an empty or invalid response.")
        )
        if is_safety_question(question):
            LOGGER.warning("Ollama diagnostic: response replaced with safety fallback")
            return SAFETY_FALLBACK_MESSAGE
        LOGGER.warning("Ollama diagnostic: response replaced with generic fallback")
        return FALLBACK_MESSAGE

    cleaned_answer = answer.strip()
    if cleaned_answer != answer:
        LOGGER.warning("Ollama diagnostic: response trimmed surrounding whitespace")
    answer = cleaned_answer
    if is_safety_question(question):
        filtered_answer = _remove_positive_safety_claims(answer)
        if filtered_answer != answer:
            LOGGER.warning("Ollama diagnostic: positive safety claims removed")
        answer = filtered_answer
        if not answer:
            LOGGER.warning(
                "Ollama diagnostic: safety-only response replaced with safety fallback"
            )
            return SAFETY_FALLBACK_MESSAGE
        warned_answer = re.sub(
            r"\bavoid touching it\b",
            "avoid touching or eating it",
            answer,
            flags=re.IGNORECASE,
        )
        if warned_answer != answer:
            LOGGER.warning("Ollama diagnostic: contact warning strengthened")
        answer = warned_answer
        if SAFETY_FALLBACK_MESSAGE.casefold() not in answer.casefold():
            answer = answer.rstrip(" \t\r\n.,;:!?")
            answer = f"{answer}. {SAFETY_FALLBACK_MESSAGE}"
            LOGGER.warning("Ollama diagnostic: plant safety warning appended")
        return answer

    if asks_for_identification(question) and len(answer.split()) == 1:
        identification = answer.strip(" \t\r\n.,!?;:")
        if identification:
            LOGGER.warning(
                "Ollama diagnostic: one-word identification expanded (model_text=one_word)"
            )
            return (
                f"The image appears to show a {identification.lower()}, but "
                "there isn't enough detail to identify it more specifically."
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
