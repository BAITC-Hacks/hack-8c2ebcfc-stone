"""Voice and language flows without microphone hardware or external APIs."""
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.data_loader import slots_catalog
from backend.state import DialogState
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def routed(language="ru", scenario="SYS_OUT_OF_SCOPE", slots=None, continuation=False):
    return {
        "scenarios": [{"scenario_id": scenario, "confidence": 0.99, "reason": "test"}],
        "alternatives": [], "language": language, "slots": slots or {},
        "is_continuation": continuation,
    }


class VoiceAppTests(unittest.TestCase):
    def setUp(self):
        self.speak = self._patch("backend.speech.speak", return_value=(b"MP3-audio", 12.0))
        self.transcribe = self._patch("backend.speech.transcribe", return_value=("Сәлеметсіз бе", 8.0))
        self.route = self._patch("backend.router.route", return_value=routed())
        self.localize = self._patch("backend.reply_language.localize_details", side_effect=lambda text, language: text)
        self.audio_input = self._patch("streamlit.audio_input", return_value=None)

    def _patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def _app(self):
        app = AppTest.from_file(APP).run(timeout=20)
        self.assertFalse(app.exception)
        return app

    def _type(self, app, text):
        app.chat_input[0].set_value(text).run(timeout=20)
        self.assertFalse(app.exception)
        return app.session_state.messages[-1]["text"]

    @staticmethod
    def _warnings(app):
        return [element.value for element in app.warning] + [element.value for element in app.error]

    def test_russian_kazakh_and_mixed_turns_reach_tts_in_answer_language(self):
        for text, detected, response_language in (
            ("Здравствуйте, как оплатить полис?", "ru", "ru"),
            ("Мен несие алғым келеді", "kk", "kk"),
            ("Здравствуйте, менің полисім бойынша сұрақ бар", "mixed", "kk"),
            ("Полис керек", "kk", "kk"),
        ):
            with self.subTest(text=text):
                self.route.return_value = routed(detected)
                app = self._app()
                reply = self._type(app, text)
                self.assertEqual(app.session_state.last_trace["language"], detected)
                self.assertEqual(app.session_state.state.response_language, response_language)
                self.speak.assert_called_with(reply, response_language)
                self.assertIn("сақтандыру" if response_language == "kk" else "Могу помочь", reply)

    def test_kazakh_missing_slots_are_spoken_using_catalog_questions(self):
        self.route.return_value = routed("kk", "SC33")
        app = self._app()
        reply = self._type(app, "Кеңсе қайда орналасқан?")
        city_prompt = next(slot["prompt"]["kk"] for slot in slots_catalog()["slots"] if slot["name"] == "city")
        self.assertIn(city_prompt, reply)
        self.assertNotIn("city", reply)
        self.speak.assert_called_with(reply, "kk")

    def test_russian_question_after_kazakh_gets_russian_reply_despite_router_error(self):
        text = "Добрый день! Сколько будет стоить обязательная страховка на машину?"
        expected = next(s["prompt"]["ru"] for s in slots_catalog()["slots"] if s["name"] == "region")
        for model_language in ("kk", "mixed"):
            for microphone in (False, True):
                with self.subTest(model_language=model_language, microphone=microphone):
                    self.audio_input.return_value = None
                    self.route.return_value = routed("kk")
                    app = self._app()
                    self._type(app, "Мен несие алғым келеді")
                    self.assertEqual(app.session_state.state.response_language, "kk")
                    self.route.return_value = {**routed(model_language, "SC01"), "response_language": "kk"}
                    if microphone:
                        self.audio_input.return_value = io.BytesIO(b"russian-question")
                        self.transcribe.return_value = (text, 8.0)
                        app.run(timeout=20)
                        self.assertFalse(app.exception)
                        reply = app.session_state.messages[-1]["text"]
                    else:
                        reply = self._type(app, text)
                    self.assertIn(expected, reply)
                    self.assertNotIn("Көлік қай қалада тіркелген?", reply)
                    self.assertEqual(app.session_state.last_trace["language"], "ru")
                    self.assertEqual(app.session_state.state.response_language, "ru")
                    self.speak.assert_called_with(reply, "ru")

    def test_numeric_identity_preserves_kazakh_response_language(self):
        self.route.return_value = routed("kk", "SC17")
        app = self._app()
        self._type(app, "Менің өтінішімнің мәртебесі қандай?")
        self.assertEqual(app.session_state.awaiting_identification, "SC17")
        reply = self._type(app, "850314300121")
        self.assertEqual(app.session_state.state.response_language, "kk")
        self.assertNotIn("Не нашла", reply)
        self.speak.assert_called_with(reply, "kk")
        self.assertEqual(self.route.call_count, 1)

    def test_microphone_transcript_is_routed_and_spoken_once(self):
        transcript = "Здравствуйте, менің полисім бойынша сұрақ бар"
        self.audio_input.return_value = io.BytesIO(b"first-recording")
        self.transcribe.return_value = (transcript, 8.0)
        self.route.return_value = routed("mixed")
        app = self._app()
        self.assertEqual(app.session_state.messages[0]["text"], transcript)
        self.assertEqual(self.route.call_args.args[0], transcript)
        self.assertEqual(app.session_state.last_trace["transcript"], transcript)
        self.assertEqual(app.session_state.last_trace["language"], "mixed")
        self.assertEqual(app.session_state.state.response_language, "kk")
        self.speak.assert_called_with(app.session_state.messages[-1]["text"], "kk")
        app.run(timeout=20)
        self.assertEqual(self.transcribe.call_count, 1)
        self.assertEqual(self.route.call_count, 1)
        self.assertEqual(len(app.session_state.messages), 2)

    def test_customer_d10_switches_from_kazakh_to_russian(self):
        dataset = Path(APP).parent / "data/case_2/voice_router_dataset/dialogs_sample.json"
        dialog = next(d for d in json.loads(dataset.read_text())["dialogs"] if d["dialog_id"] == "D10")
        turns = [turn for turn in dialog["turns"] if turn["role"] == "client"][:2]
        self.route.side_effect = [routed(t["lang"], t["scenarios"][0], t.get("slots", {}), i > 0) for i, t in enumerate(turns)]
        app = self._app()
        self._type(app, turns[0]["text"])
        self.assertEqual(app.session_state.state.response_language, "kk")
        reply = self._type(app, turns[1]["text"])
        self.assertEqual(app.session_state.state.response_language, "ru")
        self.speak.assert_called_with(reply, "ru")

    def test_multiple_missing_slots_ask_catalog_questions(self):
        self.route.return_value = routed("kk", "SC01")
        app = self._app()
        reply = self._type(app, "Көлікке міндетті сақтандыру бағасы қандай?")
        region_prompt = next(s["prompt"]["kk"] for s in slots_catalog()["slots"] if s["name"] == "region")
        self.assertIn(region_prompt, reply)

    def test_typed_cancellation_consumes_simultaneous_audio_without_replaying_it(self):
        self.route.return_value = routed("ru", "SC29", {
            "contact_field": "email", "new_value": "new@mail.example",
        })
        app = self._app()
        app.session_state.state = DialogState(client_id="C001")
        original_email = app.session_state.state.mock_data["clients"][0]["email"]
        self._type(app, "Измените адрес электронной почты")
        self.assertEqual(app.session_state.awaiting_confirmation, "SC29")
        self.route.reset_mock()
        self.speak.reset_mock()

        # Text cancellation wins over an older spoken confirmation delivered
        # by the audio widget during the same Streamlit rerun.
        self.audio_input.return_value = io.BytesIO(b"simultaneous-confirmation")
        self.transcribe.return_value = ("Да, подтверждаю", 8.0)
        reply = self._type(app, "Отмена")
        self.assertIn("отменяю", reply)
        self.assertIsNone(app.session_state.awaiting_confirmation)
        self.assertIsNone(app.session_state.state.pending_confirmation)
        self.assertEqual(app.session_state.state.mock_data["clients"][0]["email"], original_email)
        messages = list(app.session_state.messages)
        turn = app.session_state.state.turn
        app.run(timeout=20)
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state.messages, messages)
        self.assertEqual(app.session_state.state.turn, turn)
        self.assertEqual([m["text"] for m in messages if m["role"] == "user"], [
            "Измените адрес электронной почты", "Отмена",
        ])
        self.transcribe.assert_not_called()
        self.route.assert_not_called()
        self.assertEqual(self.speak.call_count, 1)

    def test_failed_stt_can_retry_same_recording_without_duplicate_turn(self):
        self.audio_input.return_value = io.BytesIO(b"retry-recording")
        self.transcribe.side_effect = [RuntimeError("STT unavailable"), ("Мен несие алғым келеді", 8.0)]
        self.route.return_value = routed("kk")
        app = self._app()
        self.assertTrue(self._warnings(app))
        self.assertEqual(app.session_state.messages, [])
        self.assertIsNone(app.session_state.last_audio_hash)
        self.route.assert_not_called()
        self.speak.assert_not_called()
        app.run(timeout=20)
        self.assertEqual(self.transcribe.call_count, 1, "Ordinary reruns should not repeat a failed API call")
        retry = [button for button in app.button if "распознавание" in button.label.lower()]
        self.assertEqual(len(retry), 1)
        retry[0].click().run(timeout=20)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state.messages), 2)
        self.assertEqual(self.route.call_count, 1)
        self.assertEqual(self.transcribe.call_count, 2)
        app.run(timeout=20)
        self.assertEqual(self.transcribe.call_count, 2)
        self.assertEqual(len(app.session_state.messages), 2)

    def test_tts_failure_preserves_reply_and_retry_does_not_repeat_backend(self):
        self.route.return_value = routed("kk", "SC33", {"city": "Almaty"})
        self.speak.side_effect = [RuntimeError("TTS unavailable"), (b"retry-MP3", 15.0)]
        app = self._app()
        reply = self._type(app, "Алматыдағы кеңсе қайда?")
        self.assertTrue(reply)
        self.assertTrue(self._warnings(app), "TTS failure must be visible after rerun")
        messages = list(app.session_state.messages)
        turn = app.session_state.state.turn
        actions = list(app.session_state.turn_actions)
        retry = [button for button in app.button if "повтор" in button.label.lower() or "қайт" in button.label.lower()]
        self.assertEqual(len(retry), 1, "A failed spoken reply needs an explicit playback retry")
        retry[0].click().run(timeout=20)
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state.last_reply_audio, b"retry-MP3")
        self.assertEqual(app.session_state.messages, messages)
        self.assertEqual(app.session_state.state.turn, turn)
        self.assertEqual(app.session_state.turn_actions, actions)
        self.assertEqual(self.route.call_count, 1)
        self.assertEqual(self.speak.call_count, 2)
        self.assertFalse(self._warnings(app))


if __name__ == "__main__":
    unittest.main()
