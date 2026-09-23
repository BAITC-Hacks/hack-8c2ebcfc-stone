from dataclasses import dataclass

RUN_THRESHOLD = 0.75
CLARIFY_THRESHOLD = 0.45


@dataclass
class Decision:
    action: str
    scenario_ids: list[str]
    clarify_options: list[str] | None = None


def decide(router_output: dict, low_confidence_streak: int) -> Decision:
    scenarios = router_output.get("scenarios", [])
    if not scenarios:
        return Decision(action="unclear", scenario_ids=[], clarify_options=[])

    top = scenarios[0]
    confidence = top.get("confidence", 0.0)

    if confidence >= RUN_THRESHOLD:
        urgent_ids = [s["scenario_id"] for s in scenarios]
        return Decision(action="run", scenario_ids=urgent_ids)

    if confidence >= CLARIFY_THRESHOLD:
        options = [s["scenario_id"] for s in scenarios[:2]]
        return Decision(action="clarify", scenario_ids=[], clarify_options=options)

    if low_confidence_streak >= 1:
        return Decision(action="handoff", scenario_ids=[])

    return Decision(action="clarify", scenario_ids=[], clarify_options=[top["scenario_id"]])
