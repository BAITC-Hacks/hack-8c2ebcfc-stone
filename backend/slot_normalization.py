"""Validate and normalize slot values returned by the LLM router."""

from backend.data_loader import slots_catalog
from backend.triage import normalize_phone


_TYPES = {item["name"]: item.get("type") for item in slots_catalog()["slots"]}


def normalize_slot(name: str, value: object) -> object | None:
    slot_type = _TYPES.get(name)
    if slot_type is None or value is None or value == "" or value == []:
        return None
    if slot_type == "list":
        if isinstance(value, str):
            return [value] if value else None
        return value if isinstance(value, list) and value else None
    if isinstance(value, list):
        if len(value) != 1:
            return None
        value = value[0]
    if name == "phone":
        return normalize_phone(str(value))
    if name in ("policy_number", "claim_number", "vehicle_plate", "culprit_vehicle_plate"):
        return str(value).strip().upper()
    if slot_type == "integer":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if slot_type == "boolean":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "yes", "да", "иә"):
            return True
        if str(value).lower() in ("false", "no", "нет", "жоқ"):
            return False
        return None
    return str(value).strip() or None
