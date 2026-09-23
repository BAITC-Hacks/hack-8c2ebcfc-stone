import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "case_2" / "voice_router_dataset"


def _load(name: str):
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@lru_cache
def scenarios() -> list[dict]:
    return _load("scenarios.json")["scenarios"]


@lru_cache
def slots_catalog() -> dict:
    return _load("slots.json")


@lru_cache
def actions_catalog() -> dict:
    return _load("actions.json")


@lru_cache
def knowledge_base() -> dict:
    return _load("knowledge_base.json")


@lru_cache
def mock_backend() -> dict:
    return _load("mock_backend.json")


@lru_cache
def scenario_by_id(scenario_id: str) -> dict | None:
    return next((s for s in scenarios() if s["scenario_id"] == scenario_id), None)
