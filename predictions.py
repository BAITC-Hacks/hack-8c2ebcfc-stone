import json
from pathlib import Path

from backend.data_loader import DATA_DIR
from backend.router import route
from backend.state import DialogState

DEV_PATH = DATA_DIR / "dev_utterances.json"
OUT_PATH = Path(__file__).resolve().parent / "predictions.json"


def main() -> None:
    dev = json.loads(DEV_PATH.read_text(encoding="utf-8"))
    predictions = {}

    for item in dev["utterances"] if "utterances" in dev else dev:
        state = DialogState()
        output = route(item["text"], state)
        scenario_ids = [s["scenario_id"] for s in output.get("scenarios", [])]
        predictions[item["id"]] = scenario_ids or ["SYS_OUT_OF_SCOPE"]
        print(item["id"], "->", scenario_ids)

    OUT_PATH.write_text(json.dumps(predictions, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {len(predictions)} predictions to {OUT_PATH}")


if __name__ == "__main__":
    main()
