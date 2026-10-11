"""Local web interface for the existing WildWhisper application."""

import base64
import logging
import tempfile
from pathlib import Path
from threading import Lock

import requests
from flask import (
    Flask,
    abort,
    jsonify,
    render_template,
    request,
    send_from_directory,
)
from werkzeug.exceptions import RequestEntityTooLarge

import app as wildwhisper

LOGGER = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_audio_lock = Lock()


def _image_suffix(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def create_app() -> Flask:
    web = Flask(__name__)
    web.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 256 * 1024

    @web.after_request
    def set_security_headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' blob: data: https://lh3.googleusercontent.com; "
            "media-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            "script-src 'self' https://cdn.tailwindcss.com; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @web.before_request
    def restrict_local_api():
        if not request.path.startswith("/api/"):
            return None
        host = request.host.split(":", 1)[0].strip("[]").lower()
        if host not in {"127.0.0.1", "localhost"}:
            abort(403)
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            abort(403)
        return None

    @web.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_error):
        return (
            jsonify(error="That image is too large. Choose an image under 10 MB."),
            413,
        )

    @web.errorhandler(403)
    def forbidden(_error):
        return (
            jsonify(
                error="The local WildWhisper service only accepts requests from this computer."
            ),
            403,
        )

    @web.get("/")
    def index():
        return render_template("stitch/home.html")

    @web.get("/explore")
    def explore_page():
        return render_template("stitch/explore.html")

    @web.get("/voice")
    def voice_page():
        return render_template("stitch/voice.html")

    @web.get("/result")
    def result_page():
        return render_template("stitch/result.html")

    @web.get("/history")
    def history_page():
        return render_template("stitch/history.html")

    @web.get("/about")
    def about_page():
        return render_template("stitch/about.html")

    @web.get("/sample/<path:filename>")
    def sample_file(filename: str):
        return send_from_directory(Path(__file__).parent / "sample", filename)

    @web.get("/api/status")
    def status():
        configured_model = wildwhisper.OLLAMA_MODEL
        try:
            response = requests.get(
                f"{wildwhisper.OLLAMA_URL}/api/tags",
                timeout=(2, 3),
            )
            response.raise_for_status()
            payload = response.json()
            models = payload.get("models", []) if isinstance(payload, dict) else []
            available_names = {
                model.get("name")
                for model in models
                if isinstance(model, dict) and isinstance(model.get("name"), str)
            }
            model_available = configured_model in available_names or (
                ":" not in configured_model
                and f"{configured_model}:latest" in available_names
            )
            online = model_available
        except (requests.RequestException, ValueError, TypeError):
            online = False
        return jsonify(
            online=online,
            label="Local AI Online" if online else "Local AI Offline",
        )

    @web.post("/api/listen")
    def listen():
        if not wildwhisper.VOSK_MODEL_PATH.is_dir():
            return jsonify(
                error="The local Vosk voice model is not installed. Follow the voice setup instructions first."
            ), 503
        try:
            question, low_confidence = (
                wildwhisper.listen_for_question_with_confidence(seconds=8)
            )
        except RuntimeError as error:
            wildwhisper.capture_exception(error)
            message = str(error)
            if "No speech was recognized." in message:
                return jsonify(
                    error="No speech was recognized. Try again in a quieter place."
                ), 400
            if any(
                detail in message
                for detail in (
                    "too quiet",
                    "clipping",
                    "recording was incomplete",
                    "recording was cut short",
                )
            ):
                return jsonify(error=message), 400
            if "permission was denied" in message:
                return jsonify(
                    error="Microphone permission was denied. Allow microphone access in your operating-system settings, then try again."
                ), 403
            if "No microphone input device" in message:
                return jsonify(
                    error="No microphone is available. Connect or enable a microphone, then try again."
                ), 503
            if "requires the sounddevice and vosk packages" in message:
                return jsonify(
                    error="Local voice input dependencies are unavailable. Reinstall the project requirements."
                ), 503
            if "speech recognition failed" in message:
                return jsonify(
                    error="Local voice recognition failed. Check the Vosk model and try again."
                ), 503
            LOGGER.warning("Voice input failed (%s)", type(error).__name__)
            return jsonify(
                error="Voice input could not start. Check the local Vosk model and try again."
            ), 503
        except (OSError, ValueError) as error:
            wildwhisper.capture_exception(error)
            LOGGER.warning("Microphone unavailable (%s)", type(error).__name__)
            return jsonify(
                error="The microphone is unavailable. Check its connection and operating-system permissions."
            ), 503
        if not isinstance(question, str) or not question.strip():
            return jsonify(
                error="No speech was recognized. Try again in a quieter place."
            ), 400
        return jsonify(question=question.strip(), low_confidence=low_confidence)

    @web.post("/api/ask")
    def ask():
        question = request.form.get("question", "").strip()
        uploaded = request.files.get("photo")
        if uploaded is None or not uploaded.filename:
            return jsonify(error="Choose a photo to explore first."), 400
        if not question:
            return jsonify(error="Add a question or ask using your voice."), 400
        if len(question) > 2000:
            return jsonify(error="Keep your question under 2,000 characters."), 400

        data = uploaded.stream.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            return jsonify(error="That image is too large. Choose an image under 10 MB."), 413
        suffix = _image_suffix(data)
        if (
            suffix is None
            or uploaded.mimetype not in ALLOWED_IMAGE_TYPES
            or ALLOWED_IMAGE_TYPES[uploaded.mimetype] != suffix
        ):
            return jsonify(
                error="That file is not a supported image. Choose a JPG, PNG, or WebP photo."
            ), 400

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="wildwhisper-",
                suffix=suffix,
                delete=False,
            ) as temporary_file:
                temporary_file.write(data)
                temporary_path = Path(temporary_file.name)

            with wildwhisper.sentry_scope(
                wildwhisper.sentry_sdk.start_transaction
                if wildwhisper.sentry_sdk is not None
                else None,
                op="gen_ai.invoke_agent",
                name="wildwhisper.web_pipeline",
            ):
                answer = wildwhisper.ask_gemma(temporary_path, question)

                audio_data = None
                local_spoken = False
                try:
                    with _audio_lock:
                        audio_path = wildwhisper.synthesize_speech(answer)
                        audio_data = base64.b64encode(
                            audio_path.read_bytes()
                        ).decode("ascii")
                except Exception as error:
                    wildwhisper.capture_exception(error)
                    LOGGER.info(
                        "ElevenLabs audio unavailable (%s)", type(error).__name__
                    )
                    local_spoken = wildwhisper.try_speak_locally(answer)

                return jsonify(
                    question=question,
                    answer=answer,
                    safety_warning=wildwhisper.is_safety_question(question),
                    audio=audio_data,
                    local_spoken=local_spoken,
                )
        except (requests.RequestException, OSError, RuntimeError, ValueError, TypeError) as error:
            wildwhisper.capture_exception(error)
            LOGGER.warning("Local inference failed (%s)", type(error).__name__)
            return jsonify(
                error="WildWhisper could not reach the local vision model. Check that Ollama is running with Moondream available."
            ), 503
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as error:
                    LOGGER.warning(
                        "Temporary image cleanup failed (%s)", type(error).__name__
                    )

    @web.errorhandler(500)
    def server_error(_error):
        return jsonify(
            error="WildWhisper encountered a problem. Please try again."
        ), 500

    return web


app = create_app()


if __name__ == "__main__":
    wildwhisper.initialize_sentry()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
