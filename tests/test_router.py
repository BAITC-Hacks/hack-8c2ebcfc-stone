import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.router import _normalize_output, route
from backend.state import DialogState


def item(scenario_id, confidence=0.9):
    return {"scenario_id": scenario_id, "confidence": confidence, "reason": "test"}


def response(output):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))])


class RouterTests(unittest.TestCase):
    def test_urgent_first_and_deduplication(self):
        output = _normalize_output({"scenarios": [item("SC27", .8), item("SC11"), item("SC27", .95)]})
        self.assertEqual([x["scenario_id"] for x in output["scenarios"]], ["SC11", "SC27"])
        self.assertEqual(output["scenarios"][1]["confidence"], .95)

    def test_invalid_predictions_raise(self):
        for entries in ([], [item("SC99")], [item("SC01", 1.2)], [item("SC01", True)]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                _normalize_output({"scenarios": entries})

    @patch("backend.router._get_client")
    def test_invalid_empty_reply_is_retried(self, get_client):
        create = get_client.return_value.chat.completions.create
        create.side_effect = [response({"scenarios": []}), response({"scenarios": [item("SC25")]})]
        self.assertEqual(route("Проверьте полис", DialogState())["scenarios"][0]["scenario_id"], "SC25")
        self.assertEqual(create.call_count, 2)

    @patch("backend.router._get_client")
    def test_llm_choice_is_not_rewritten_by_keywords(self, get_client):
        examples = (
            ("Нужна справка для посольства", "SYS_OUT_OF_SCOPE"),
            ("Какие документы нужны после затопления?", "SC14"),
            ("Нужна туристическая страховка, как оплатить полис?", "SC06"),
        )
        create = get_client.return_value.chat.completions.create
        for utterance, chosen in examples:
            create.return_value = response({"scenarios": [item(chosen)], "slots": {}})
            with self.subTest(utterance=utterance):
                got = route(utterance, DialogState())
                self.assertEqual([x["scenario_id"] for x in got["scenarios"]], [chosen])

    @patch("backend.router._get_client")
    def test_route_passes_conversation_and_catalog(self, get_client):
        result = {"scenarios": [item("SC21")], "language": "kk", "slots": {}, "is_continuation": True}
        get_client.return_value.chat.completions.create.return_value = response(result)
        state = DialogState(active_scenario="SC21", language="kk")
        state.record_turn("bot", "Қай дәрігерге және қай күнге?")
        self.assertEqual(route("Ертең", state), result)
        request = get_client.return_value.chat.completions.create.call_args.kwargs
        self.assertIn("Қай дәрігерге және қай күнге?", request["messages"][1]["content"])
        self.assertIn("doctor_specialty", request["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
