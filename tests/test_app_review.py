import os
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("OPENAI_API_KEY", "test-only")

from backend import router
from backend.state import DialogState
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


class AppReview(unittest.TestCase):
    def setUp(self):
        original_route = router.route
        self.addCleanup(setattr, router, "route", original_route)
        for target, options in (
            ("backend.speech.speak", {"return_value": (b"audio", 1.0)}),
            ("backend.reply_language.localize_details", {"side_effect": lambda text, lang: text}),
        ):
            mock = patch(target, **options)
            mock.start()
            self.addCleanup(mock.stop)

    @staticmethod
    def _route(ids, slots=None, continuation=False):
        return {
            "scenarios": [{"scenario_id": sid, "confidence": 0.99, "reason": "test"} for sid in ids],
            "alternatives": [], "language": "ru", "slots": slots or {},
            "is_continuation": continuation,
        }

    def _contact_preview(self):
        router.route = lambda text, state: {
            "scenarios": [{"scenario_id": "SC29", "confidence": 0.99, "reason": "update contact"}],
            "alternatives": [], "language": "ru",
            "slots": {"contact_field": "email", "new_value": "new@mail.example"},
            "is_continuation": False,
        }
        app = AppTest.from_file(APP).run(timeout=20)
        app.session_state.state = DialogState(client_id="C001")
        app.chat_input[0].set_value("Обновите почту").run(timeout=20)
        self.assertFalse(app.exception)
        self.assertIn("n***@mail.example", app.session_state.messages[-1]["text"])
        self.assertNotEqual(app.session_state.state.mock_data["clients"][0]["email"], "new@mail.example")
        return app

    def test_negative_confirmation_never_updates(self):
        for answer in ("не надо", "пока нет", "не подтверждаю", "Жоқ"):
            app = self._contact_preview()
            app.chat_input[0].set_value(answer).run(timeout=20)
            self.assertFalse(app.exception)
            self.assertNotEqual(app.session_state.state.mock_data["clients"][0]["email"], "new@mail.example", answer)

    def test_positive_confirmation_updates_once(self):
        for answer in ("Иә, растаймын.", "Верно."):
            app = self._contact_preview()
            app.chat_input[0].set_value(answer).run(timeout=20)
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state.state.mock_data["clients"][0]["email"], "new@mail.example")
            self.assertIsNone(app.session_state.awaiting_confirmation)

    def test_operator_interrupts_confirmation(self):
        app = self._contact_preview()
        app.chat_input[0].set_value("Соедините с оператором").run(timeout=20)
        self.assertFalse(app.exception)
        self.assertNotEqual(app.session_state.state.mock_data["clients"][0]["email"], "new@mail.example")
        self.assertIsNone(app.session_state.awaiting_confirmation)
        self.assertIn("оператором", app.session_state.messages[-1]["text"])

    def test_urgent_request_interrupts_confirmation(self):
        app = self._contact_preview()
        router.route = lambda *_: self._route(["SC38"], {"fraud_details": "мошенник просил код"})
        app.chat_input[0].set_value("Мошенник попросил код карты").run(timeout=20)
        self.assertFalse(app.exception)
        self.assertIsNone(app.session_state.awaiting_confirmation)
        self.assertNotEqual(app.session_state.state.mock_data["clients"][0]["email"], "new@mail.example")

    def test_new_coverage_intent_replaces_pending_identification(self):
        def route(text, _state):
            if "терапевтке" in text:
                return {**self._route(["SC21"], {"doctor_specialty": "therapist"}), "language": "kk"}
            return {**self._route(["SC22"], {"service_name": "МРТ"}), "language": "mixed"}
        router.route = route
        app = AppTest.from_file(APP).run(timeout=20)
        app.chat_input[0].set_value("Маған ДМС бойынша терапевтке жазылу керек").run(timeout=20)
        self.assertEqual(app.session_state.awaiting_identification, "SC21")
        app.chat_input[0].set_value("Менің полисім бойынша МРТ покрывается ма?").run(timeout=20)
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state.awaiting_identification, "SC22")
        self.assertEqual(app.session_state.last_trace["scenarios"][0]["scenario_id"], "SC22")
        self.assertIn("ЖСН", app.session_state.messages[-1]["text"])
        self.assertNotIn("Не нашла клиента", app.session_state.messages[-1]["text"])

    def test_sms_retry_only_sends_sms(self):
        router.route = lambda *_: self.fail("router must not re-run the scenario for SMS retry")
        app = AppTest.from_file(APP).run(timeout=20)
        app.session_state.state.pending_sms = {"scenario_id": "SC27", "phone": "+77010000001"}
        initial_policies = len(app.session_state.state.mock_data["policies"])
        app.chat_input[0].set_value("Повторите SMS").run(timeout=20)
        self.assertFalse(app.exception)
        self.assertIsNone(app.session_state.state.pending_sms)
        self.assertEqual(len(app.session_state.state.mock_data["policies"]), initial_policies)
        self.assertEqual(app.session_state.turn_actions[0]["action"], "send_sms")

    def test_system_scenario_second_in_queue(self):
        router.route = lambda *_: self._route(["SC33", "SYS_OUT_OF_SCOPE"], {"city": "Almaty"})
        app = AppTest.from_file(APP).run(timeout=20)
        app.chat_input[0].set_value("Где офис и можно ли взять кредит?").run(timeout=20)
        self.assertFalse(app.exception)
        reply = app.session_state.messages[-1]["text"]
        self.assertIn("Abai", reply)
        self.assertIn("Могу помочь только", reply)

    def test_kazakh_system_reply(self):
        router.route = lambda *_: {**self._route(["SYS_OUT_OF_SCOPE"]), "language": "kk"}
        app = AppTest.from_file(APP).run(timeout=20)
        app.chat_input[0].set_value("Мен несие алғым келеді").run(timeout=20)
        self.assertFalse(app.exception)
        self.assertIn("сақтандыру", app.session_state.messages[-1]["text"])

    def test_declining_confirmation_runs_next_intent(self):
        router.route = lambda *_: self._route(["SC29", "SC31"], {"contact_field": "email", "new_value": "new@mail.example"})
        app = AppTest.from_file(APP).run(timeout=20)
        app.session_state.state = DialogState(client_id="C001")
        app.chat_input[0].set_value("Измените почту и расскажите об оплате").run(timeout=20)
        app.chat_input[0].set_value("Жоқ").run(timeout=20)
        self.assertFalse(app.exception)
        self.assertNotEqual(app.session_state.state.mock_data["clients"][0]["email"], "new@mail.example")
        self.assertIn("Жақсы,", app.session_state.messages[-1]["text"])
        self.assertTrue(any(a["action"] == "kb_lookup" for a in app.session_state.turn_actions))
        self.assertNotIn("{", app.session_state.messages[-1]["text"])

    def test_new_intent_with_slot_answer_is_queued(self):
        def route(text, _state):
            if text == "Адрес офиса":
                return self._route(["SC33"])
            return self._route(["SC33", "SC31"], {"city": "Almaty"}, continuation=True)
        router.route = route
        app = AppTest.from_file(APP).run(timeout=20)
        app.chat_input[0].set_value("Адрес офиса").run(timeout=20)
        app.chat_input[0].set_value("Алматы, и какие способы оплаты?").run(timeout=20)
        self.assertFalse(app.exception)
        self.assertTrue(any(a["action"] == "get_offices" for a in app.session_state.turn_actions))
        self.assertTrue(any(a["action"] == "kb_lookup" for a in app.session_state.turn_actions))
        self.assertEqual(app.session_state.queued_scenarios, [])


if __name__ == "__main__":
    unittest.main()
