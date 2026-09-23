import re
import time

import streamlit as st

from backend.actions_mock import ActionError, call_action
from backend.data_loader import scenario_by_id
from backend.decision_policy import decide
from backend.executor import run_scenario
from backend.router import route
from backend.state import DialogState
from backend.triage import detect_language, normalize_phone

st.set_page_config(page_title="Saqta Voice Router", layout="wide")

if "state" not in st.session_state:
    st.session_state.state = DialogState()
if "messages" not in st.session_state:
    st.session_state.messages = []
if "low_confidence_streak" not in st.session_state:
    st.session_state.low_confidence_streak = 0
if "awaiting_identification" not in st.session_state:
    st.session_state.awaiting_identification = None
if "awaiting_confirmation" not in st.session_state:
    st.session_state.awaiting_confirmation = None

st.title("Saqta Insurance — Voice Router")

IIN_RE = re.compile(r"\b\d{12}\b")
YES_WORDS = {"да", "ага", "угу", "подтверждаю", "ок", "окей", "конечно", "хорошо", "yes", "ok"}
NO_WORDS = {"нет", "не", "отмена", "отменить", "no", "cancel"}


def _extract_identity(text: str) -> dict:
    ident = {}
    phone = normalize_phone(text)
    if phone:
        ident["phone"] = phone
    iin_match = IIN_RE.search(text)
    if iin_match:
        ident["iin"] = iin_match.group()
    return ident


def _closing_message(scenario: dict, state: DialogState, executed_actions: list[dict]) -> str:
    merged = {}
    for a in executed_actions:
        if isinstance(a.get("result"), dict):
            merged.update(a["result"])
    lang = state.language if state.language in ("ru", "kk") else "ru"
    template = scenario.get("responses", {}).get(lang, {}).get("closing")
    if not template:
        return f"Готово: {merged}" if merged else "Готово."
    try:
        return template.format(**merged)
    except (KeyError, IndexError):
        return template


def handle_result(scenario_id: str, result: dict, state: DialogState) -> str:
    status = result.get("status")
    scenario = scenario_by_id(scenario_id) or {}

    if status == "need_identification":
        st.session_state.awaiting_identification = scenario_id
        return "Для этой операции нужно вас идентифицировать. Назовите, пожалуйста, номер телефона или ИИН."

    if status == "need_slots":
        missing = result.get("slots", [])
        return "Уточните, пожалуйста: " + ", ".join(missing)

    if status == "need_confirmation":
        st.session_state.awaiting_confirmation = scenario_id
        previews = [
            f"{a['action']}: {a['result']}"
            for a in result.get("actions", [])
            if a.get("mode") == "preview" and "result" in a
        ]
        preview_text = "; ".join(previews) if previews else "детали уточняются"
        return f"Проверьте, пожалуйста: {preview_text}. Подтверждаете? (да/нет)"

    if status == "action_error":
        return (
            f"Не получилось выполнить действие «{result.get('action')}» "
            f"({result.get('code')}). Соединяю с оператором."
        )

    if status == "done":
        st.session_state.awaiting_confirmation = None
        return _closing_message(scenario, state, result.get("actions", []))

    if status == "error":
        return f"Не удалось определить сценарий: {result.get('reason')}"

    return str(result)


chat_col, trace_col = st.columns([2, 1])

with chat_col:
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.write(m["text"])

    user_input = st.chat_input("Скажите что-нибудь клиенту...")

    if user_input:
        state = st.session_state.state
        st.session_state.messages.append({"role": "user", "text": user_input})
        state.language = detect_language(user_input)

        t0 = time.perf_counter()
        router_output = None
        decision_action = None
        result = None

        if st.session_state.awaiting_identification:
            scenario_id = st.session_state.awaiting_identification
            ident = _extract_identity(user_input)
            try:
                found = call_action("find_client", **ident)
                state.client_id = found["client_id"]
                st.session_state.awaiting_identification = None
                result = run_scenario(scenario_id, state)
                reply = handle_result(scenario_id, result, state)
            except ActionError:
                reply = "Не нашла клиента с такими данными. Повторите телефон или ИИН, пожалуйста."

        elif st.session_state.awaiting_confirmation:
            scenario_id = st.session_state.awaiting_confirmation
            answer = user_input.strip().lower()
            if any(w in answer for w in YES_WORDS):
                st.session_state.awaiting_confirmation = None
                result = run_scenario(scenario_id, state, confirmed=True)
                reply = handle_result(scenario_id, result, state)
            elif any(w in answer for w in NO_WORDS):
                st.session_state.awaiting_confirmation = None
                state.pending_confirmation = None
                reply = "Хорошо, отменяю операцию."
            else:
                reply = "Пожалуйста, подтвердите (да/нет)."

        else:
            router_output = route(user_input, state)
            for slot_name, slot_value in router_output.get("slots", {}).items():
                state.set_slot(slot_name, slot_value)

            decision = decide(router_output, st.session_state.low_confidence_streak)
            decision_action = decision.action

            if decision.action == "run":
                scenario_id = decision.scenario_ids[0]
                result = run_scenario(scenario_id, state)
                reply = handle_result(scenario_id, result, state)
                st.session_state.low_confidence_streak = 0
            elif decision.action == "clarify":
                reply = "Уточните, пожалуйста, что именно вас интересует: " + ", ".join(decision.clarify_options)
                st.session_state.low_confidence_streak += 1
            else:
                reply = "Соединяю вас с оператором."
                st.session_state.low_confidence_streak = 0

        t_total = time.perf_counter() - t0

        state.record_turn("client", user_input)
        state.record_turn("bot", reply)
        st.session_state.messages.append({"role": "assistant", "text": reply})

        st.session_state.last_trace = {
            "turn": state.turn,
            "transcript": user_input,
            "language": state.language,
            "scenarios": (router_output or {}).get("scenarios", []),
            "alternatives": (router_output or {}).get("alternatives", []),
            "reason": [s.get("reason") for s in (router_output or {}).get("scenarios", [])],
            "decision": decision_action,
            "slots": state.slots,
            "actions": (result or {}).get("actions", []) if result else [],
            "latency_ms": {"total": round(t_total * 1000, 1)},
        }
        st.rerun()

with trace_col:
    st.subheader("Trace panel")
    st.json(st.session_state.get("last_trace", {}))
