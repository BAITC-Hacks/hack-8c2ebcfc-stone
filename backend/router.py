import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from backend.data_loader import scenarios
from backend.state import DialogState

load_dotenv()

_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_MODEL = os.getenv("ROUTER_MODEL", "gpt-4o-mini")


def _scenario_catalog_prompt() -> str:
    lines = []
    for s in scenarios():
        not_this_if = "; ".join(
            f"{r.get('condition', '')} -> {r.get('use_instead', '')}"
            for r in s.get("not_this_if", [])
        )
        lines.append(
            f"{s['scenario_id']} ({s['domain']}/{s['category']}, priority={s['priority']}): "
            f"{s['description']}"
            + (f" | not this if: {not_this_if}" if not_this_if else "")
        )
    return "\n".join(lines)


SYSTEM_PROMPT = """You are the scenario router for Saqta Insurance's voice agent.
Given a client utterance and dialog state, decide which scenario(s) apply.
Reply ONLY with JSON matching this contract:
{
  "scenarios": [{"scenario_id": "SCxx", "confidence": 0.0-1.0, "reason": "..."}],
  "alternatives": [{"scenario_id": "SCxx", "confidence": 0.0-1.0}],
  "language": "ru|kk|mixed",
  "slots": {},
  "is_continuation": false
}
Saqta Insurance only sells and services insurance (auto, health, travel, property, accident, corporate).
It does NOT offer loans, life insurance, pensions, weather, jobs, or anything unrelated to insurance servicing.

The "scenarios" list must NEVER be empty. Always put exactly one entry there:
- A real scenario_id (SC01-SC40) if you can identify what the client wants, even with medium confidence.
- "SYS_OUT_OF_SCOPE" if the request has nothing to do with Saqta's insurance products or services.
- "SYS_UNCLEAR" only if the request IS about Saqta but you truly cannot tell which scenario — never for off-topic requests.
- "SYS_GOODBYE" if the client is ending the conversation.

If the client's turn contains more than one distinct request (multi-intent), list ALL of them in "scenarios",
in the order the client said them, but with any priority="urgent" scenario moved first.
Urgent scenarios (priority=urgent) must be prioritized when present.
"""


def route(utterance: str, state: DialogState) -> dict:
    user_prompt = (
        f"Scenario catalog:\n{_scenario_catalog_prompt()}\n\n"
        f"Dialog state: active_scenario={state.active_scenario}, "
        f"stack={state.scenario_stack}, known_slots={state.slots}\n\n"
        f"Client utterance: {utterance}"
    )

    resp = _client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    return json.loads(resp.choices[0].message.content)
