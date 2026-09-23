"""Offline contracts for multilingual speech API requests."""
import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend import speech


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.audio.transcriptions.create.return_value = SimpleNamespace(text="Төлем туралы сұрақ бар")
        self.client.audio.speech.create.return_value = SimpleNamespace(content=b"MP3-audio")
        client_patch = patch("backend.speech._get_client", return_value=self.client)
        client_patch.start()
        self.addCleanup(client_patch.stop)

    def test_transcription_preserves_russian_kazakh_and_mixed_text(self):
        samples = (
            "Здравствуйте, как оплатить полис?",
            "Сәлеметсіз бе, полисті қалай төлеймін?",
            "Здравствуйте, менің полисім бойынша сұрақ бар",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.client.audio.transcriptions.create.return_value.text = sample
                transcript, elapsed_ms = speech.transcribe(io.BytesIO(b"recording"))
                self.assertEqual(transcript, sample)
                self.assertGreaterEqual(elapsed_ms, 0)
        request = self.client.audio.transcriptions.create.call_args.kwargs
        self.assertEqual(request["model"], "gpt-4o-transcribe")
        self.assertEqual(request["response_format"], "json")
        self.assertNotIn("language", request)
        self.assertEqual(request["file"][1], b"recording")
        self.assertTrue(request.get("prompt"))

    def test_empty_transcription_is_rejected(self):
        for value in ("", "  \n  "):
            with self.subTest(value=value):
                self.client.audio.transcriptions.create.return_value.text = value
                with self.assertRaises(ValueError):
                    speech.transcribe(io.BytesIO(b"recording"))

    def test_transcription_errors_propagate_to_visible_app_handler(self):
        self.client.audio.transcriptions.create.side_effect = RuntimeError("STT unavailable")
        with self.assertRaisesRegex(RuntimeError, "STT unavailable"):
            speech.transcribe(io.BytesIO(b"recording"))

    def test_tts_has_explicit_language_and_audio_format(self):
        for language, text, marker in (
            ("ru", "Назовите номер полиса.", "russian"),
            ("kk", "Полис нөмірін айтыңызшы.", "kazakh"),
        ):
            with self.subTest(language=language):
                audio, elapsed_ms = speech.speak(text, language=language)
                self.assertEqual(audio, b"MP3-audio")
                self.assertGreaterEqual(elapsed_ms, 0)
                request = self.client.audio.speech.create.call_args.kwargs
                self.assertEqual(request["model"], "gpt-4o-mini-tts")
                self.assertEqual(request["response_format"], "mp3")
                self.assertEqual(request["input"], text)
                self.assertIn(marker, request["instructions"].lower())

    def test_tts_errors_propagate_to_retry_handler(self):
        self.client.audio.speech.create.side_effect = RuntimeError("TTS unavailable")
        with self.assertRaisesRegex(RuntimeError, "TTS unavailable"):
            speech.speak("Полис нөмірін айтыңызшы.", language="kk")


if __name__ == "__main__":
    unittest.main()
