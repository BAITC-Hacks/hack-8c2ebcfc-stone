import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from backend.data_loader import scenarios, slots_catalog
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
        examples = s.get("examples", {})
        example_ru = next(iter(examples.get("ru", [])), "")
        example_kk = next(iter(examples.get("kk", [])), "")
        lines.append(
            f"{s['scenario_id']} ({s['domain']}/{s['category']}, priority={s['priority']}): "
            f"{s['description']}"
            f" | required slots: {', '.join(s.get('slots', {}).get('required', [])) or 'none'}"
            f" | optional slots: {', '.join(s.get('slots', {}).get('optional', [])) or 'none'}"
            f" | examples: {example_ru}; {example_kk}"
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

The "scenarios" list must NEVER be empty. Include each distinct request once:
- A real scenario_id (SC01-SC40) if you can identify what the client wants, even with medium confidence.
- "SYS_OUT_OF_SCOPE" if the request has nothing to do with Saqta's insurance products or services.
- "SYS_UNCLEAR" only if the request IS about Saqta but you truly cannot tell which scenario — never for off-topic requests.
- "SYS_GOODBYE" if the client is ending the conversation.

If the client's turn contains more than one distinct request (multi-intent), list ALL of them in "scenarios",
in the order the client said them, but with any priority="urgent" scenario moved first.
Urgent scenarios (priority=urgent) must be prioritized when present.
Do not put alternative interpretations or SYS_UNCLEAR alongside a concrete scenario in "scenarios";
put plausible but unrequested alternatives in "alternatives" only.
Distinguish an intention to buy OGPO now (SC02) from asking only for a quote (SC01).
Booking a doctor under an employer's existing DMS is SC21, not corporate sales SC10.
Asking whether DMS covers tests is SC22; a missing DMS card inside the app is SC24.
Questions about documents for a claim are SC18, even when the damage is to property.
A suspicious caller, payment demand, or SMS link claiming to represent Saqta is SC38.
A certificate for a visa or embassy is SC39, even if travel is the reason for the certificate.
Do not invent a second scenario when the client only supplies slots or confirms the active one.
Mentioning drivers while pricing OGPO is still SC01, not adding a driver to an existing policy (SC04).
Supplying driver IINs while pricing OGPO is not a purchase request (SC02).
When an active travel purchase (SC06) is confirmed, do not add OGPO purchase (SC02).
If a turn both confirms the active appointment (SC21) and asks about coverage (SC22), keep SC21 first.
An existing claim's status is SC17, even when property damage is mentioned; a new claim is SC14.
Questions about additional documents or where to send them are SC18, not SC17 or SC14.
After discussing a suspicious call, a request to check whether a policy is active is SC25, not another fraud report.
Extract only slot names from the chosen scenario catalog entries. Preserve the slot types
(for example drivers_iin is a list), normalize dates as YYYY-MM-DD relative to 2026-10-01,
and do not guess missing values. All scalar slots (including phone, vehicle plate and IIN)
must be strings, never arrays; omit missing slots instead of returning empty arrays.
Normalize phone numbers to +7 followed by 10 digits, with no spaces.
For a short answer supplying a requested slot, keep the
active scenario and set is_continuation=true.
"""


def route(utterance: str, state: DialogState) -> dict:
    enum_slots = [
        f"{slot['name']}: {', '.join(map(str, slot['values']))}"
        for slot in slots_catalog()["slots"] if slot.get("values")
    ]
    user_prompt = (
        f"Scenario catalog:\n{_scenario_catalog_prompt()}\n\n"
        f"Allowed enum slot values (map Russian/Kazakh names to these codes):\n"
        f"{'; '.join(enum_slots)}\n\n"
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
