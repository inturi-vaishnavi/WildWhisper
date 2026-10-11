from io import BytesIO
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

import app
import webapp


class AskGemmaTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.photo_path = Path(self.temp_dir.name) / "leaves.jpg"
        self.photo_path.write_bytes(b"mock image bytes")

    def tearDown(self):
        self.temp_dir.cleanup()

    def ask_with_response(self, question, model_response):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"response": model_response}
        response.raise_for_status.return_value = None

        with patch("app.requests.post", return_value=response) as post:
            answer = app.ask_gemma(self.photo_path, question)

        return answer, post.call_args.kwargs["json"]

    def test_safety_answer_keeps_leaf_details_and_adds_caution(self):
        answer, request_data = self.ask_with_response(
            "Is this plant safe to touch?",
            "The leaves are bright green and oval, with serrated edges, "
            "but they appear safe to touch.",
        )

        self.assertIn("bright green and oval", answer)
        self.assertIn("serrated edges", answer)
        self.assertNotIn("appear safe to touch", answer)
        self.assertIn(app.SAFETY_FALLBACK_MESSAGE, answer)
        self.assertIn(
            "first describe their observable shape, color, edges, arrangement, "
            "and texture",
            request_data["prompt"],
        )
        self.assertEqual(request_data["options"]["num_predict"], 100)

    def test_eating_question_preserves_details_without_safety_claim(self):
        answer, _ = self.ask_with_response(
            "Can I eat this plant?",
            "The leaves are narrow and deep green, with smooth edges.",
        )

        self.assertTrue(answer.startswith("The leaves are narrow and deep green"))
        self.assertIn("avoid touching or eating it", answer)

    def test_empty_safety_response_uses_caution_without_inventing_details(self):
        answer, _ = self.ask_with_response("Is this safe to eat?", "")

        self.assertEqual(answer, app.SAFETY_FALLBACK_MESSAGE)

    def test_existing_touch_warning_is_extended_without_repeating_it(self):
        answer, _ = self.ask_with_response(
            "Is this plant safe to touch?",
            "I can't determine whether this is safe from the image alone. "
            "Until the plant is reliably identified, avoid touching it.",
        )

        self.assertEqual(answer, app.SAFETY_FALLBACK_MESSAGE)

    def test_non_safety_response_is_preserved(self):
        answer, _ = self.ask_with_response(
            "Describe the leaves.",
            "The leaves are broad, green, and deeply lobed.",
        )

        self.assertEqual(answer, "The leaves are broad, green, and deeply lobed.")

    def test_tentative_identification_and_observations_are_preserved(self):
        model_text = (
            "The leaves are small, fan-shaped, and light green with delicate "
            "edges. They may be a maidenhair fern, though the image is not "
            "clear enough to confirm."
        )

        answer, request_data = self.ask_with_response(
            "What plant is this?",
            model_text,
        )

        self.assertEqual(answer, model_text)
        self.assertIn(
            "first describe their observable shape, color, edges, arrangement, "
            "and texture",
            request_data["prompt"],
        )
        self.assertIn(
            "Then give a tentative identification only if the visible evidence supports one",
            request_data["prompt"],
        )

    def test_one_word_plant_response_is_logged_as_post_processing(self):
        with self.assertLogs("app", level="WARNING") as logs:
            answer, _ = self.ask_with_response(
                "What plant is this?",
                "Plant",
            )

        self.assertEqual(
            answer,
            "The image appears to show a plant, but there isn't enough detail "
            "to identify it more specifically.",
        )
        self.assertTrue(any("HTTP response received" in line for line in logs.output))
        self.assertTrue(
            any(
                f"model={app.OLLAMA_MODEL}" in line and "status=200" in line
                for line in logs.output
            )
        )
        self.assertTrue(any("non_empty=True" in line for line in logs.output))
        self.assertTrue(
            any("one-word identification expanded" in line for line in logs.output)
        )

    def test_model_generic_fallback_is_not_replaced(self):
        model_text = (
            "This appears to be a plant, but I can't reliably identify it "
            "more specifically from this image alone."
        )

        with self.assertLogs("app", level="WARNING") as logs:
            answer, _ = self.ask_with_response(
                "What plant is this?",
                model_text,
            )

        self.assertEqual(answer, model_text)
        self.assertFalse(
            any("Response post-processing" in line for line in logs.output)
        )

    def test_safety_filter_keeps_leaf_observations_and_adds_separate_warning(self):
        answer, _ = self.ask_with_response(
            "Is this plant safe to touch?",
            "The leaves are narrow and pale green with smooth edges. "
            "They appear safe to touch.",
        )

        self.assertTrue(answer.startswith("The leaves are narrow and pale green"))
        self.assertIn(". I can't determine whether this is safe", answer)
        self.assertNotIn("appear safe to touch", answer)
        self.assertNotIn("edges. .", answer)

    def test_empty_response_logs_fallback_replacement(self):
        with self.assertLogs("app", level="WARNING") as logs:
            answer, _ = self.ask_with_response(
                "What plant is this?",
                "",
            )

        self.assertEqual(answer, app.FALLBACK_MESSAGE)
        self.assertTrue(any("non_empty=False" in line for line in logs.output))
        self.assertTrue(
            any("response replaced with generic fallback" in line for line in logs.output)
        )

    def test_web_api_returns_observations_without_calling_elevenlabs(self):
        model_response = Mock()
        model_response.status_code = 200
        model_response.json.return_value = {
            "response": "The leaves are heart-shaped and dark green, "
            "with smooth edges."
        }
        model_response.raise_for_status.return_value = None
        client = webapp.create_app().test_client()

        with (
            patch("app.requests.post", return_value=model_response),
            patch(
                "app.synthesize_speech",
                side_effect=RuntimeError("Speech disabled in test"),
            ),
            patch("app.try_speak_locally", return_value=False),
            patch("app.capture_exception"),
        ):
            response = client.post(
                "/api/ask",
                data={
                    "question": "Is this plant safe to touch?",
                    "photo": (
                        BytesIO(b"\xff\xd8\xffmock image"),
                        "leaves.jpg",
                        "image/jpeg",
                    ),
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("heart-shaped and dark green", payload["answer"])
        self.assertIn(app.SAFETY_FALLBACK_MESSAGE, payload["answer"])
        self.assertTrue(payload["safety_warning"])
        self.assertIsNone(payload["audio"])

    def test_web_api_encodes_one_mocked_synthesis_result(self):
        model_response = Mock()
        model_response.status_code = 200
        model_response.json.return_value = {"response": "A short local answer."}
        model_response.raise_for_status.return_value = None
        audio_path = Path(self.temp_dir.name) / "response.mp3"
        audio_path.write_bytes(b"mock mp3 data")
        client = webapp.create_app().test_client()

        with (
            patch("app.requests.post", return_value=model_response),
            patch("app.synthesize_speech", return_value=audio_path) as synthesize,
            patch("app.try_speak_locally") as speak_locally,
        ):
            response = client.post(
                "/api/ask",
                data={
                    "question": "Describe this plant.",
                    "photo": (
                        BytesIO(b"\xff\xd8\xffmock image"),
                        "leaves.jpg",
                        "image/jpeg",
                    ),
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["audio"], "bW9jayBtcDMgZGF0YQ==")
        synthesize.assert_called_once_with("A short local answer.")
        speak_locally.assert_not_called()


class ListenForQuestionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.model_path = Path(self.temp_dir.name)
        positions = np.arange(8 * 44100, dtype=np.float64)
        self.recording = np.sin(2 * np.pi * 440 * positions / 44100)
        self.recording = (self.recording * 10000).astype(np.int16).reshape(-1, 1)
        self.device = {
            "default_samplerate": 44100.0,
            "max_input_channels": 1,
            "index": 1,
        }
        self.sounddevice = types.ModuleType("sounddevice")
        self.sounddevice.PortAudioError = type("PortAudioError", (Exception,), {})
        self.sounddevice.query_devices = Mock(return_value=self.device)
        self.sounddevice.check_input_settings = Mock()
        self.sounddevice.rec = Mock(return_value=self.recording)
        self.sounddevice.wait = Mock()

        self.recognizer = Mock()
        self.recognizer.FinalResult.return_value = json.dumps(
            {"text": "identify this leaf"}
        )
        self.vosk = types.ModuleType("vosk")
        self.vosk.Model = Mock(return_value=object())
        self.vosk.KaldiRecognizer = Mock(return_value=self.recognizer)

        self.modules = patch.dict(
            sys.modules,
            {"sounddevice": self.sounddevice, "vosk": self.vosk},
        )
        self.modules.start()
        self.model_path_patch = patch("app.VOSK_MODEL_PATH", self.model_path)
        self.model_path_patch.start()

    def tearDown(self):
        self.model_path_patch.stop()
        self.modules.stop()
        self.temp_dir.cleanup()

    def test_records_int16_at_device_rate_and_returns_vosk_transcript(self):
        question = app.listen_for_question(seconds=8)

        self.assertEqual(question, "identify this leaf")
        self.sounddevice.rec.assert_called_once_with(
            8 * 44100,
            samplerate=44100,
            channels=1,
            dtype="int16",
        )
        self.sounddevice.wait.assert_called_once_with()
        self.sounddevice.check_input_settings.assert_called_once_with(
            device=1,
            channels=1,
            dtype="int16",
            samplerate=44100,
        )
        self.vosk.KaldiRecognizer.assert_called_once_with(
            self.vosk.Model.return_value,
            app.VOSK_SAMPLE_RATE,
        )
        audio_bytes = self.recognizer.AcceptWaveform.call_args.args[0]
        pcm = np.frombuffer(audio_bytes, dtype="<i2")
        self.assertEqual(pcm.size, 8 * app.VOSK_SAMPLE_RATE)
        self.assertGreater(np.max(np.abs(pcm)), 0)

    def test_stereo_audio_is_resampled_to_mono_16_bit_pcm(self):
        positions = np.arange(44100, dtype=np.float64)
        voice = (np.sin(2 * np.pi * 440 * positions / 44100) * 10000).astype(
            np.int16
        )
        recording = np.column_stack((voice, np.zeros_like(voice)))

        pcm_bytes = app._prepare_vosk_audio(recording, 44100)
        pcm = np.frombuffer(pcm_bytes, dtype="<i2")

        self.assertEqual(pcm.size, app.VOSK_SAMPLE_RATE)
        self.assertEqual(len(pcm_bytes), app.VOSK_SAMPLE_RATE * 2)
        self.assertGreater(np.max(np.abs(pcm)), 9000)

    def test_silence_is_rejected_before_vosk_recognition(self):
        self.sounddevice.rec.return_value = np.zeros_like(self.recording)

        with self.assertRaisesRegex(RuntimeError, "input was too quiet"):
            app.listen_for_question()

        self.vosk.KaldiRecognizer.assert_not_called()

    def test_clipped_audio_requests_a_new_recording(self):
        clipped = np.full_like(self.recording, 32767)
        self.sounddevice.rec.return_value = clipped

        with self.assertRaisesRegex(RuntimeError, "audio is clipping"):
            app.listen_for_question()

    def test_empty_transcript_is_not_returned(self):
        self.recognizer.FinalResult.return_value = '{"text": "  "}'

        with self.assertRaisesRegex(RuntimeError, "No speech was recognized"):
            app.listen_for_question()

    def test_missing_microphone_is_reported(self):
        self.sounddevice.query_devices.side_effect = self.sounddevice.PortAudioError(
            "No input device"
        )

        with self.assertRaisesRegex(RuntimeError, "No microphone input device"):
            app.listen_for_question()

    def test_microphone_permission_error_is_reported(self):
        self.sounddevice.rec.side_effect = self.sounddevice.PortAudioError(
            "Access denied"
        )

        with self.assertRaisesRegex(RuntimeError, "permission was denied"):
            app.listen_for_question()

    def test_vosk_failure_is_reported_without_a_transcript(self):
        self.vosk.KaldiRecognizer.side_effect = RuntimeError("bad model")

        with self.assertRaisesRegex(RuntimeError, "speech recognition failed"):
            app.listen_for_question()

    def test_non_object_vosk_result_is_treated_as_empty_transcript(self):
        self.recognizer.FinalResult.return_value = "[]"

        with self.assertRaisesRegex(RuntimeError, "No speech was recognized"):
            app.listen_for_question()


class ListenRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = webapp.create_app().test_client()
        self.model_path = patch(
            "webapp.wildwhisper.VOSK_MODEL_PATH",
            Mock(is_dir=Mock(return_value=True)),
        )
        self.model_path.start()
        self.addCleanup(self.model_path.stop)
        self.capture = patch("webapp.wildwhisper.capture_exception")
        self.capture.start()
        self.addCleanup(self.capture.stop)

    def test_returns_recognized_transcript(self):
        with patch(
            "webapp.wildwhisper.listen_for_question",
            return_value="What kind of tree is this?",
        ):
            response = self.client.post("/api/listen")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["question"], "What kind of tree is this?")

    def test_does_not_return_an_empty_transcript(self):
        with patch("webapp.wildwhisper.listen_for_question", return_value=" "):
            response = self.client.post("/api/listen")

        self.assertEqual(response.status_code, 400)
        self.assertIn("No speech was recognized", response.get_json()["error"])

    def test_microphone_permission_error_has_a_clear_message(self):
        with patch(
            "webapp.wildwhisper.listen_for_question",
            side_effect=RuntimeError("Microphone permission was denied."),
        ):
            response = self.client.post("/api/listen")

        self.assertEqual(response.status_code, 403)
        self.assertIn("permission was denied", response.get_json()["error"])

    def test_quiet_microphone_audio_has_a_retry_message(self):
        with patch(
            "webapp.wildwhisper.listen_for_question",
            side_effect=RuntimeError(
                "The microphone input was too quiet. Check the input level and try again."
            ),
        ):
            response = self.client.post("/api/listen")

        self.assertEqual(response.status_code, 400)
        self.assertIn("too quiet", response.get_json()["error"])

    def test_recognition_failure_has_a_clear_message(self):
        with patch(
            "webapp.wildwhisper.listen_for_question",
            side_effect=RuntimeError("Local speech recognition failed"),
        ):
            response = self.client.post("/api/listen")

        self.assertEqual(response.status_code, 503)
        self.assertIn("recognition failed", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
