from copy import deepcopy

from backend.actions_mock import call_action, ActionError, action_spec
from backend.data_loader import scenario_by_id
from backend.state import DialogState

_PRODUCT_PREFIXES = ("ogpo", "casco", "travel", "property", "accident", "dms")
_CITY_NAMES = {
    "алматы": "Almaty", "астана": "Astana", "шымкент": "Shymkent",
    "караганда": "Karaganda", "қарағанды": "Karaganda",
    "актобе": "Aktobe", "ақтөбе": "Aktobe", "атырау": "Atyrau",
    "павлодар": "Pavlodar", "өскемен": "Oskemen", "усть-каменогорск": "Oskemen",
}
_KB_SCENARIO_TOPICS = {
    "SC03": "products.casco", "SC07": "products.property",
    "SC08": "products.accident", "SC09": "products.dms",
    "SC11": "claims.road_accident_now", "SC24": "products.dms.e_card",
    "SC31": "payments", "SC32": "bonus_malus", "SC34": "app_help",
    "SC38": "fraud_policy",
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


def _populate_known_slots(scenario_id: str, state: DialogState) -> None:
    scenario = scenario_by_id(scenario_id)
    if not scenario:
        return
    required = scenario.get("slots", {}).get("required", [])
    if "policy_number" in required and not state.slots.get("policy_number") and state.client_id:
        policies = [p for p in state.mock_data["policies"] if p.get("client_id") == state.client_id and p.get("status") != "cancelled"]
        if len(policies) == 1:
            state.set_slot("policy_number", policies[0]["policy_number"])
    if "product_type" in required and not state.slots.get("product_type") and state.slots.get("claim_number"):
        claim = next((c for c in state.mock_data["claims"] if c["claim_number"] == state.slots["claim_number"]), None)
        if claim and claim.get("claim_type"):
            state.set_slot("product_type", claim["claim_type"])


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
    # A victim's earlier policy must never replace the culprit's OGPO policy.
    if scenario["scenario_id"] == "SC12":
        kwargs.pop("policy_number", None)
    if isinstance(kwargs.get("city"), str):
        kwargs["city"] = _CITY_NAMES.get(kwargs["city"].strip().lower(), kwargs["city"])
    if isinstance(kwargs.get("region"), str):
        kwargs["region"] = {"алматы": "almaty", "астана": "astana"}.get(
            kwargs["region"].strip().lower(), kwargs["region"]
        )
    if state.client_id:
        kwargs["client_id"] = state.client_id
        if not kwargs.get("phone"):
            client = next((c for c in state.mock_data["clients"] if c["client_id"] == state.client_id), None)
            if client and client.get("phone"):
                kwargs["phone"] = client["phone"]
    if "product_type" not in kwargs:
        product_type = _infer_product_type(scenario)
        if product_type:
            kwargs["product_type"] = product_type
    if "kb_lookup" in scenario.get("actions", []):
        scenario_id = scenario["scenario_id"]
        if scenario_id == "SC18":
            product = kwargs.get("product_type")
            document_product = "ogpo_victim" if product == "ogpo" else product
            kwargs["topic"] = (
                f"claims.documents.{document_product}"
                if document_product != "dms" else "claims.submission"
            )
        elif scenario_id in _KB_SCENARIO_TOPICS:
            kwargs["topic"] = _KB_SCENARIO_TOPICS[scenario_id]
        elif not kwargs.get("topic"):
            kwargs["topic"] = scenario.get("slug", "").replace("_", " ")
    kwargs["_store"] = state.mock_data
    if scenario.get("handoff"):
        kwargs["queue"] = scenario["handoff"]["queue"]
    return _apply_slot_aliases(kwargs)


def _should_transfer(scenario_id: str, kwargs: dict, results: list[dict]) -> bool:
    detail = " ".join(str(kwargs.get(k, "")) for k in (
        "incident_description", "complaint_text", "fraud_details", "topic"
    )).lower()
    if scenario_id == "SC11":
        return bool(kwargs.get("injured")) or "оператор" in detail
    if scenario_id == "SC13":
        return any(word in detail for word in ("угон", "украл", "theft", "total loss", "полная гибель"))
    if scenario_id == "SC14":
        return any(word in detail for word in ("пожар", "fire")) and bool(kwargs.get("injured"))
    if scenario_id == "SC19":
        return any(word in detail for word in ("оператор", "человек", "person"))
    if scenario_id == "SC30":
        return any(a.get("result", {}).get("payment_status") == "charged_policy_not_issued" for a in results)
    if scenario_id == "SC34":
        return any(word in detail for word in ("не помог", "не реш", "still", "оператор"))
    if scenario_id == "SC35":
        return any(word in detail for word in ("оператор", "человек", "нагруб", "груб", "ужас"))
    if scenario_id == "SC38":
        return any(word in detail for word in ("код", "card", "карт", "cvc", "cvv", "otp"))
    return True


def _mask(value: object) -> str:
    text = str(value)
    if "@" in text:
        name, domain = text.split("@", 1)
        return f"{name[:1]}***@{domain}"
    if text.startswith("+7") and len(text) >= 8:
        return text[:2] + "******" + text[-3:]
    if len(text) == 12 and text.isdigit():
        return "********" + text[-4:]
    return text


def _confirmation_summary(scenario: dict, kwargs: dict, previews: list[dict]) -> str:
    fields = []
    for key in ("policy_number", "vehicle_plate", "new_driver_iin", "trip_country",
                "trip_start", "trip_end", "travelers_count", "incident_date",
                "city", "preferred_date", "doctor_specialty", "contact_field",
                "new_value", "cancel_reason", "phone"):
        if kwargs.get(key) is not None:
            value = "[адрес скрыт]" if key == "new_value" and kwargs.get("contact_field") == "address" else _mask(kwargs[key])
            fields.append(f"{key}: {value}")
    for item in previews:
        result = item.get("result", {})
        for key in ("price", "refund_amount", "clinic_name", "slot_datetime", "extra_premium"):
            if key in result:
                fields.append(f"{key}: {result[key]}")
    actions = ", ".join(a for a in scenario.get("actions", []) if (action_spec(a) or {}).get("irreversible"))
    return f"{actions}: " + ("; ".join(fields) if fields else "подтверждение операции")


def run_scenario(scenario_id: str, state: DialogState, confirmed: bool = False) -> dict:
    scenario = scenario_by_id(scenario_id)
    if scenario is None:
        return {"status": "error", "reason": "unknown scenario"}

    if scenario.get("requires_identification") and not state.client_id:
        ident = {k: state.slots[k] for k in ("phone", "iin") if state.slots.get(k)}
        if ident:
            try:
                state.client_id = call_action("find_client", _store=state.mock_data, **ident)["client_id"]
            except ActionError:
                pass
        if not state.client_id:
            return {"status": "need_identification"}

    _populate_known_slots(scenario_id, state)
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
            if action_name in ("cancel_policy", "book_appointment"):
                try:
                    preview_result = call_action(action_name, preview=True, **kwargs)
                    preview_actions.append({"action": action_name, "mode": "preview", "result": preview_result})
                except ActionError as e:
                    return {"status": "action_error", "code": e.code, "action": action_name}
                continue
            if action_name in ("send_sms", "transfer_to_operator") or (spec and spec.get("irreversible")):
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

        state.pending_confirmation = {
            "scenario_id": scenario_id, "slots": deepcopy(state.slots),
            "client_id": state.client_id,
        }
        return {
            "status": "need_confirmation", "actions": preview_actions,
            "confirmation": _confirmation_summary(scenario, kwargs, preview_actions),
        }

    if confirmed and state.pending_confirmation and (
        state.pending_confirmation["scenario_id"] != scenario_id
        or state.pending_confirmation["slots"] != state.slots
        or state.pending_confirmation["client_id"] != state.client_id
    ):
        return {"status": "confirmation_expired"}

    executed_actions = []
    for action_name in action_names:
        if action_name == "transfer_to_operator" and not _should_transfer(scenario_id, kwargs, executed_actions):
            executed_actions.append({"action": action_name, "mode": "skipped"})
            continue
        if action_name == "send_sms" and not kwargs.get("phone"):
            # Several informational scenarios offer SMS only when a phone is
            # available; the scenario must still finish without that option.
            executed_actions.append({"action": action_name, "mode": "skipped"})
            state.pending_sms = {"scenario_id": scenario_id, "phone": None}
            continue
        try:
            result = call_action(action_name, **kwargs)
            executed_actions.append({"action": action_name, "mode": "execute", "result": result})
            if action_name == "send_sms":
                state.pending_sms = None
            if action_name.startswith("calc_") and isinstance(result, dict) and "price" in result:
                kwargs["premium"] = result["price"]
        except ActionError as e:
            if action_name == "send_sms":
                executed_actions.append({"action": action_name, "mode": "failed", "code": e.code})
                state.pending_sms = {"scenario_id": scenario_id, "phone": kwargs.get("phone")}
                continue
            return {"status": "action_error", "code": e.code, "action": action_name}
        except NotImplementedError:
            executed_actions.append({"action": action_name, "mode": "stub"})

    state.pending_confirmation = None
    state.push_scenario(scenario_id)
    return {"status": "done", "actions": executed_actions}
