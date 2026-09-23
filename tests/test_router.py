import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.router import _normalize_output, route
from backend.state import DialogState


def item(scenario_id, confidence=0.9):
    return {"scenario_id": scenario_id, "confidence": confidence, "reason": "test"}


class RouterTests(unittest.TestCase):
    @patch("backend.router._get_client")
    def test_invalid_empty_reply_is_retried(self, get_client):
        create = get_client.return_value.chat.completions.create
        create.side_effect = [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"scenarios":[]}'))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "scenarios": [item("SC25")], "slots": {}, "is_continuation": False,
            })))]),
        ]
        self.assertEqual(route("Проверьте полис", DialogState())["scenarios"][0]["scenario_id"], "SC25")
        self.assertEqual(create.call_count, 2)

    def test_urgent_first_preserves_other_requests_and_order(self):
        output = _normalize_output({"scenarios": [item("SC27"), item("SC11"), item("SC04")]})
        self.assertEqual([s["scenario_id"] for s in output["scenarios"]], ["SC11", "SC27", "SC04"])

    def test_deduplicate_without_losing_other_intents(self):
        output = _normalize_output({"scenarios": [item("SC27", .8), item("SC04"), item("SC27", .95)]})
        self.assertEqual([s["scenario_id"] for s in output["scenarios"]], ["SC27", "SC04"])
        self.assertEqual(output["scenarios"][0]["confidence"], .95)

    def test_invalid_predictions_are_not_silently_scored_as_out_of_scope(self):
        for entries in [[], [item("SC99")], [item("SC01", 1.2)], [item("SC01", True)]]:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                _normalize_output({"scenarios": entries})

    def test_system_intents_are_supported(self):
        for scenario_id in ["SYS_OUT_OF_SCOPE", "SYS_UNCLEAR", "SYS_GOODBYE"]:
            self.assertEqual(_normalize_output({"scenarios": [item(scenario_id)]})["scenarios"][0]["scenario_id"], scenario_id)

    @patch("backend.router._get_client")
    def test_route_passes_conversation_and_bilingual_catalog(self, get_client):
        result = {"scenarios": [item("SC21")], "language": "kk", "slots": {}, "is_continuation": True}
        get_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(result)))])
        state = DialogState(active_scenario="SC21", language="kk")
        state.record_turn("bot", "Қай дәрігерге және қай күнге?")
        self.assertEqual(route("Ертең", state), result)
        request = get_client.return_value.chat.completions.create.call_args.kwargs
        catalog_prompt = request["messages"][0]["content"]
        prompt = request["messages"][1]["content"]
        self.assertIn("Қай дәрігерге және қай күнге?", prompt)
        self.assertIn('"kk":', catalog_prompt)
        self.assertIn("doctor_specialty", catalog_prompt)
        self.assertIn("active_scenario=SC21", prompt)


if __name__ == "__main__":
    unittest.main()
