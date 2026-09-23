import os
import unittest
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-only")

from backend import router
from backend.state import DialogState
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


class AppReview(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
