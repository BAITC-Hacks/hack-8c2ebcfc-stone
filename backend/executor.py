from backend.actions_mock import call_action, ActionError
from backend.data_loader import scenario_by_id
from backend.state import DialogState


def missing_slots(scenario_id: str, state: DialogState) -> list[str]:
    scenario = scenario_by_id(scenario_id)
    if scenario is None:
        return []
    required = scenario.get("slots", {}).get("required", [])
    return [s for s in required if s not in state.slots]


def run_scenario(scenario_id: str, state: DialogState) -> dict:
    scenario = scenario_by_id(scenario_id)
    if scenario is None:
        return {"status": "error", "reason": "unknown scenario"}

    if scenario.get("requires_identification") and not state.client_id:
        return {"status": "need_identification"}

    missing = missing_slots(scenario_id, state)
    if missing:
        return {"status": "need_slots", "slots": missing}

    executed_actions = []
    for action_name in scenario.get("actions", []):
        try:
            mode = "preview" if scenario.get("requires_confirmation") else "execute"
            result = call_action(action_name, **state.slots)
            executed_actions.append({"action": action_name, "mode": mode, "result": result})
        except ActionError as e:
            return {"status": "action_error", "code": e.code, "action": action_name}
        except NotImplementedError:
            executed_actions.append({"action": action_name, "mode": "stub"})

    state.push_scenario(scenario_id)
    return {"status": "done", "actions": executed_actions}
