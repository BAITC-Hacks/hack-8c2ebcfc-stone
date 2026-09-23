import unittest

from backend.decision_policy import decide


def router_output(*entries):
    return {
        "scenarios": [
            {"scenario_id": scenario_id, "confidence": confidence}
            for scenario_id, confidence in entries
        ]
    }


class DecisionPolicyTests(unittest.TestCase):
    def test_high_confidence_runs_all_routed_intents(self):
        decision = decide(router_output(("SC38", 0.96), ("SC39", 0.91)), 0)
        self.assertEqual(decision.action, "run")
        self.assertEqual(decision.scenario_ids, ["SC38", "SC39"])

    def test_medium_confidence_requests_clarification(self):
        decision = decide(router_output(("SC18", 0.70), ("SC14", 0.55)), 0)
        self.assertEqual(decision.action, "clarify")
        self.assertEqual(decision.clarify_options, ["SC18", "SC14"])

    def test_second_low_confidence_turn_hands_off(self):
        first = decide(router_output(("SC18", 0.30)), 0)
        second = decide(router_output(("SC18", 0.30)), 1)
        self.assertEqual(first.action, "clarify")
        self.assertEqual(second.action, "handoff")

    def test_empty_router_output_is_explicitly_unclear(self):
        decision = decide({"scenarios": []}, 0)
        self.assertEqual(decision.action, "unclear")
        self.assertEqual(decision.scenario_ids, [])


if __name__ == "__main__":
    unittest.main()
