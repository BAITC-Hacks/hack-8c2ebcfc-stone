from backend.actions_mock import call_action, ActionError, action_spec
from backend.data_loader import mock_backend, scenario_by_id
from backend.state import DialogState

_PRODUCT_PREFIXES = ("ogpo", "casco", "travel", "property", "accident", "dms")
_CITY_NAMES = {
    "алматы": "Almaty", "астана": "Astana", "шымкент": "Shymkent",
    "караганда": "Karaganda", "қарағанды": "Karaganda",
    "актобе": "Aktobe", "ақтөбе": "Aktobe", "атырау": "Atyrau",
    "павлодар": "Pavlodar", "өскемен": "Oskemen", "усть-каменогорск": "Oskemen",
}


def missing_slots(scenario_id: str, state: DialogState) -> list[str]:
    scenario = scenario_by_id(scenario_id)
    if scenario is None:
        return []
    required = scenario.get("slots", {}).get("required", [])
    return [
        s for s in required
        if s not in state.slots or state.slots[s] is None or state.slots[s] == "" or state.slots[s] == []
    ]


def _infer_product_type(scenario: dict) -> str | None:
    # No scenario carries product_type as an explicit slot - it's implicit in
    # the scenario itself (ogpo_buy -> ogpo, casco_claim -> casco, ...), so we
    # derive it from the scenario slug for actions that need it
    # (create_policy, create_claim, calc_* helpers called generically, etc.)
    slug = scenario.get("slug", "")
    first = slug.split("_")[0]
    return first if first in _PRODUCT_PREFIXES else None


# Some scenarios name a slot differently from the action parameter it feeds
# (e.g. SC04's new_driver_iin / SC12's culprit_vehicle_plate), because the
# slot name carries dialog context the action itself doesn't need - so we
# alias the scenario-specific name onto the canonical action parameter name
# whenever the canonical one hasn't been filled directly.
_SLOT_ALIASES = {
    "iin": ["new_driver_iin"],
    "vehicle_plate": ["culprit_vehicle_plate"],
}


def _apply_slot_aliases(kwargs: dict) -> dict:
    kwargs = dict(kwargs)
    for canonical, alt_names in _SLOT_ALIASES.items():
        if canonical not in kwargs:
            for alt in alt_names:
                if alt in kwargs:
                    kwargs[canonical] = kwargs[alt]
                    break
    if "iin" not in kwargs and "drivers_iin" in kwargs:
        drivers_iin = kwargs["drivers_iin"]
        if isinstance(drivers_iin, list) and drivers_iin:
            kwargs["iin"] = drivers_iin[0]
        elif isinstance(drivers_iin, str):
            kwargs["iin"] = drivers_iin
    return kwargs


def _inputs_ready(spec: dict, kwargs: dict) -> bool:
    # spec["inputs"] entries may be "a|b" (either works, e.g. "policy_number|vehicle_plate")
    for inp in spec.get("inputs", []):
        alts = inp.split("|")
        if not any(kwargs.get(alt) for alt in alts):
            return False
    return True


def _missing_irreversible_inputs(action_names: list[str], kwargs: dict) -> list[str]:
    missing: list[str] = []
    for name in action_names:
        spec = action_spec(name)
        if not spec or not spec.get("irreversible"):
            continue
        for inp in spec.get("inputs", []):
            alts = inp.split("|")
            if not any(kwargs.get(a) for a in alts) and alts[0] not in missing:
                missing.append(alts[0])
    return missing


def _call_kwargs(state: DialogState, scenario: dict) -> dict:
    kwargs = dict(state.slots)
    if isinstance(kwargs.get("city"), str):
        kwargs["city"] = _CITY_NAMES.get(kwargs["city"].strip().lower(), kwargs["city"])
    if isinstance(kwargs.get("region"), str):
        kwargs["region"] = {"алматы": "almaty", "астана": "astana"}.get(
            kwargs["region"].strip().lower(), kwargs["region"]
        )
    if state.client_id:
        kwargs["client_id"] = state.client_id
        if not kwargs.get("phone"):
            client = next((c for c in mock_backend()["clients"] if c["client_id"] == state.client_id), None)
            if client and client.get("phone"):
                kwargs["phone"] = client["phone"]
    if "product_type" not in kwargs:
        product_type = _infer_product_type(scenario)
        if product_type:
            kwargs["product_type"] = product_type
    if "topic" not in kwargs and "kb_lookup" in scenario.get("actions", []):
        # kb_lookup scenarios (SC31 payment_methods, SC38 fraud_report, ...)
        # never carry an explicit "topic" slot - the topic is implicit in
        # which scenario matched, so derive a search string from the
        # scenario's own slug/category for actions_mock.kb_lookup's keyword
        # matching (SC40 terms_explanation is the exception: it has its own
        # explicit required "topic" slot, so it's unaffected by this).
        kwargs["topic"] = f"{scenario.get('category', '')} {scenario.get('slug', '')}".replace("_", " ").strip()
    return _apply_slot_aliases(kwargs)


def run_scenario(scenario_id: str, state: DialogState, confirmed: bool = False) -> dict:
    scenario = scenario_by_id(scenario_id)
    if scenario is None:
        return {"status": "error", "reason": "unknown scenario"}

    if scenario.get("requires_identification") and not state.client_id:
        return {"status": "need_identification"}

    missing = missing_slots(scenario_id, state)
    if missing:
        return {"status": "need_slots", "slots": missing}

    action_names = scenario.get("actions", [])
    has_irreversible = any((action_spec(name) or {}).get("irreversible") for name in action_names)
    needs_confirmation = bool(scenario.get("requires_confirmation")) and has_irreversible

    kwargs = _call_kwargs(state, scenario)

    # An irreversible action can't be silently deferred to "pending" the way
    # a preview action can, and it must not fail at confirm time just
    # because one of its inputs happens to be an *optional* scenario slot
    # (e.g. SC06 travel_buy lists "phone" as optional, but create_policy
    # can't run without it) - so before offering confirmation, make sure
    # every irreversible action's own required inputs are actually filled.
    extra_missing = [
        s for s in _missing_irreversible_inputs(action_names, kwargs) if s not in missing
    ]
    if extra_missing:
        return {"status": "need_slots", "slots": extra_missing}

    if needs_confirmation and not confirmed:
        preview_actions = []
        for action_name in action_names:
            spec = action_spec(action_name)
            if action_name == "send_sms" or (spec and spec.get("irreversible")):
                # Notifications belong to the confirmed pass as well.
                preview_actions.append({"action": action_name, "mode": "pending"})
                continue
            if spec and not _inputs_ready(spec, kwargs):
                # an optional slot this action needs (e.g. send_sms's phone)
                # hasn't been filled yet - defer it to the confirmed pass
                # instead of surfacing a spurious error mid-preview
                preview_actions.append({"action": action_name, "mode": "pending"})
                continue
            try:
                result = call_action(action_name, **kwargs)
                preview_actions.append({"action": action_name, "mode": "preview", "result": result})
            except ActionError as e:
                return {"status": "action_error", "code": e.code, "action": action_name}
            except NotImplementedError:
                preview_actions.append({"action": action_name, "mode": "stub"})

        state.pending_confirmation = {"scenario_id": scenario_id, "slots": dict(state.slots)}
        return {"status": "need_confirmation", "actions": preview_actions}

    executed_actions = []
    for action_name in action_names:
        if action_name == "send_sms" and not kwargs.get("phone"):
            # Several informational scenarios offer SMS only when a phone is
            # available; the scenario must still finish without that option.
            executed_actions.append({"action": action_name, "mode": "skipped"})
            continue
        try:
            result = call_action(action_name, **kwargs)
            executed_actions.append({"action": action_name, "mode": "execute", "result": result})
        except ActionError as e:
            return {"status": "action_error", "code": e.code, "action": action_name}
        except NotImplementedError:
            executed_actions.append({"action": action_name, "mode": "stub"})

    state.pending_confirmation = None
    state.push_scenario(scenario_id)
    return {"status": "done", "actions": executed_actions}
