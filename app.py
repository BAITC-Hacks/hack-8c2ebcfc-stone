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
if "awaiting_slots" not in st.session_state:
    st.session_state.awaiting_slots = None
if "queued_scenarios" not in st.session_state:
    st.session_state.queued_scenarios = []

st.title("Saqta Insurance — Voice Router")

IIN_RE = re.compile(r"\b\d{12}\b")
YES_WORDS = {"да", "ага", "угу", "подтверждаю", "ок", "окей", "конечно", "хорошо", "yes", "ok", "иә"}
NO_WORDS = {"нет", "отмена", "отменить", "no", "cancel", "жоқ"}


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
    merged = dict(state.slots)
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
    st.session_state.turn_actions.extend(result.get("actions", []))

    if status == "need_identification":
        st.session_state.awaiting_identification = scenario_id
        return "Для этой операции нужно вас идентифицировать. Назовите, пожалуйста, номер телефона или ИИН."

    if status == "need_slots":
        st.session_state.awaiting_slots = scenario_id
        missing = result.get("slots", [])
        return "Уточните, пожалуйста: " + ", ".join(missing)

    if status == "need_confirmation":
        st.session_state.awaiting_slots = None
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
        st.session_state.awaiting_slots = None
        st.session_state.awaiting_confirmation = None
        reply = _closing_message(scenario, state, result.get("actions", []))
        if st.session_state.queued_scenarios:
            next_id = st.session_state.queued_scenarios.pop(0)
            next_result = run_scenario(next_id, state)
            reply += " " + handle_result(next_id, next_result, state)
        return reply

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
        st.session_state.turn_actions = []
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
                for slot_name, slot_value in ident.items():
                    state.set_slot(slot_name, slot_value)
                st.session_state.awaiting_identification = None
                result = run_scenario(scenario_id, state)
                reply = handle_result(scenario_id, result, state)
            except ActionError:
                reply = "Не нашла клиента с такими данными. Повторите телефон или ИИН, пожалуйста."

        elif st.session_state.awaiting_confirmation:
            scenario_id = st.session_state.awaiting_confirmation
            answer_words = set(re.findall(r"[\w]+", user_input.lower()))
            if answer_words & YES_WORDS and not answer_words & NO_WORDS:
                st.session_state.awaiting_confirmation = None
                result = run_scenario(scenario_id, state, confirmed=True)
                reply = handle_result(scenario_id, result, state)
            elif answer_words & NO_WORDS and not answer_words & YES_WORDS:
                st.session_state.awaiting_confirmation = None
                state.pending_confirmation = None
                reply = "Хорошо, отменяю операцию."
            else:
                reply = "Пожалуйста, подтвердите (да/нет)."

        else:
            if st.session_state.awaiting_slots:
                state.active_scenario = st.session_state.awaiting_slots
            router_output = route(user_input, state)
            for slot_name, slot_value in router_output.get("slots", {}).items():
                state.set_slot(slot_name, slot_value)

            decision = decide(router_output, st.session_state.low_confidence_streak)
            decision_action = decision.action

            if decision.action == "run":
                is_slot_continuation = bool(
                    router_output.get("is_continuation") and st.session_state.awaiting_slots
                )
                scenario_id = (
                    st.session_state.awaiting_slots
                    if is_slot_continuation
                    else decision.scenario_ids[0]
                )
                if not is_slot_continuation:
                    st.session_state.queued_scenarios = decision.scenario_ids[1:]
                if scenario_id == "SYS_OUT_OF_SCOPE":
                    reply = "Могу помочь только с продуктами и услугами Saqta Insurance."
                elif scenario_id == "SYS_UNCLEAR":
                    reply = "Уточните, пожалуйста, с каким страховым вопросом вам помочь?"
                elif scenario_id == "SYS_GOODBYE":
                    reply = "До свидания!"
                else:
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
            "actions": st.session_state.turn_actions,
            "latency_ms": {"total": round(t_total * 1000, 1)},
        }
        st.rerun()

with trace_col:
    st.subheader("Trace panel")
    st.json(st.session_state.get("last_trace", {}))
