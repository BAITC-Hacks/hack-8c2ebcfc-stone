import re
import time

import streamlit as st

from backend.actions_mock import ActionError, call_action
from backend.confirmation import classify_confirmation
from backend.data_loader import scenario_by_id
from backend.decision_policy import decide
from backend.executor import run_scenario
from backend.router import route
from backend.state import DialogState
from backend.triage import detect_language, detect_urgency, normalize_phone

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
CLAIM_RE = re.compile(r"\bCL-\d{6}\b", re.IGNORECASE)
POLICY_RE = re.compile(r"\bSQ-(?:OGPO|CASCO|TRVL|PROP|NS|DMS)-\d{6}\b", re.IGNORECASE)


def _interruption(text: str) -> str | None:
    words = set(re.findall(r"\w+", text.lower()))
    if words & {"оператор", "оператором", "человеком", "человека", "адаммен", "операторға"}:
        return "operator"
    if detect_urgency(text) and any(w in words for w in ("сейчас", "только", "қазір", "шетелде", "мошенник", "алаяқ")):
        return "urgent"
    if "давайте потом" in text.lower() or "сначала" in words:
        return "urgent"
    if words & {"отмена", "отменить", "отменяю", "жоқ"}:
        return "cancel"
    return None


def _extract_identity(text: str) -> dict:
    ident = {}
    phone = normalize_phone(text)
    if phone:
        ident["phone"] = phone
    iin_match = IIN_RE.search(text)
    if iin_match:
        ident["iin"] = iin_match.group()
    claim_match = CLAIM_RE.search(text)
    if claim_match:
        ident["claim_number"] = claim_match.group().upper()
    policy_match = POLICY_RE.search(text)
    if policy_match:
        ident["policy_number"] = policy_match.group().upper()
    return ident


def _sms_retry_requested(text: str, state: DialogState) -> bool:
    if not state.pending_sms:
        return False
    lower = text.lower()
    if any(word in lower for word in ("sms", "смс", "хабарлама")):
        return True
    return bool(state.pending_sms.get("awaiting_phone") and normalize_phone(text))


def _closing_message(scenario: dict, state: DialogState, executed_actions: list[dict]) -> str:
    merged = dict(state.slots)
    for a in executed_actions:
        if isinstance(a.get("result"), dict):
            merged.update(a["result"])
    lang = state.language if state.language in ("ru", "kk") else "ru"
    template = scenario.get("responses", {}).get(lang, {}).get("closing")
    if not template:
        return f"Готово: {merged}" if merged else "Готово."
    scenario_id = scenario.get("scenario_id")
    if scenario_id == "SC06" and merged.get("policy_number"):
        if lang == "kk":
            return f"{merged['policy_number']} полисі рәсімделді. Бағасы {merged.get('price', 'нақтыланады')} теңге."
        return f"Полис {merged['policy_number']} оформлен. Стоимость {merged.get('price', 'уточняется')} тенге."
    sms_sent = any(a.get("action") == "send_sms" and a.get("mode") == "execute" for a in executed_actions)
    if "send_sms" in scenario.get("actions", []) and not sms_sent:
        if scenario_id == "SC02":
            return f"Полис {merged.get('policy_number', '')} оформлен. SMS не отправлена; при необходимости уточните номер."
        if scenario_id == "SC27":
            return f"Полис {merged.get('policy_number', '')} продлён. Стоимость {merged.get('price', 'уточняется')} тенге. SMS не отправлена."
        if scenario_id in ("SC12", "SC14", "SC16"):
            return f"Заявление {merged.get('claim_number', '')} зарегистрировано. SMS не отправлена."
        if scenario_id == "SC23":
            return f"Партнёрские клиники: {merged.get('clinics', [])}."
        if scenario_id == "SC18":
            return f"Документы для обращения: {merged.get('answer', [])}."
        if scenario_id == "SC11":
            return "Сфотографируйте место происшествия и автомобили. SMS с инструкцией не отправлена."
    try:
        return template.format(**merged)
    except (KeyError, IndexError):
        return "Операция завершена. Подробности доступны у оператора."


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
        return f"Проверьте, пожалуйста: {result.get('confirmation', 'детали операции')}. Подтверждаете? (да/нет)"

    if status == "confirmation_expired":
        st.session_state.awaiting_confirmation = None
        return "Данные операции изменились. Уточним их и запросим подтверждение заново."

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
        waiting = bool(
            st.session_state.awaiting_identification
            or st.session_state.awaiting_confirmation
            or st.session_state.awaiting_slots
        )
        interruption = _interruption(user_input) if waiting else None

        if interruption in ("operator", "cancel", "urgent"):
            previous = (
                st.session_state.awaiting_identification
                or st.session_state.awaiting_confirmation
                or st.session_state.awaiting_slots
            )
            st.session_state.awaiting_identification = None
            st.session_state.awaiting_confirmation = None
            st.session_state.awaiting_slots = None
            st.session_state.queued_scenarios = []
            state.pending_confirmation = None
            if previous:
                state.active_scenario = previous

        if interruption == "operator":
            transfer = call_action("transfer_to_operator", _store=state.mock_data, queue="operator_general")
            st.session_state.turn_actions.append({"action": "transfer_to_operator", "mode": "execute", "result": transfer})
            decision_action = "handoff"
            reply = "Соединяю с оператором и передаю контекст вашего обращения."

        elif interruption == "cancel":
            reply = "Хорошо, отменяю текущую операцию."

        elif _sms_retry_requested(user_input, state):
            phone = normalize_phone(user_input) or state.pending_sms.get("phone")
            if not phone:
                state.pending_sms["awaiting_phone"] = True
                reply = "Назовите номер телефона для повторной отправки SMS."
            else:
                try:
                    sent = call_action("send_sms", _store=state.mock_data, phone=phone)
                    st.session_state.turn_actions.append({"action": "send_sms", "mode": "execute", "result": sent})
                    state.pending_sms = None
                    reply = "SMS отправлена повторно."
                except ActionError as error:
                    state.pending_sms["phone"] = phone
                    reply = f"Не удалось отправить SMS ({error.code}). Основная операция уже выполнена."

        elif st.session_state.awaiting_identification:
            scenario_id = st.session_state.awaiting_identification
            ident = _extract_identity(user_input)
            try:
                if ident.get("phone") or ident.get("iin"):
                    found = call_action("find_client", _store=state.mock_data, **ident)
                    state.client_id = found["client_id"]
                elif ident.get("claim_number"):
                    claim = next((c for c in state.mock_data["claims"] if c["claim_number"] == ident["claim_number"]), None)
                    if not claim or not claim.get("client_id"):
                        raise ActionError("not_found", "claim not found")
                    state.client_id = claim["client_id"]
                elif ident.get("policy_number"):
                    policy = next((p for p in state.mock_data["policies"] if p["policy_number"] == ident["policy_number"]), None)
                    if not policy or not policy.get("client_id"):
                        raise ActionError("not_found", "policy not found")
                    state.client_id = policy["client_id"]
                else:
                    raise ActionError("not_found", "identity not supplied")
                for slot_name, slot_value in ident.items():
                    state.set_slot(slot_name, slot_value)
                st.session_state.awaiting_identification = None
                result = run_scenario(scenario_id, state)
                reply = handle_result(scenario_id, result, state)
            except ActionError:
                reply = "Не нашла клиента с такими данными. Повторите телефон или ИИН, пожалуйста."

        elif st.session_state.awaiting_confirmation:
            scenario_id = st.session_state.awaiting_confirmation
            answer = classify_confirmation(user_input)
            if answer == "yes":
                st.session_state.awaiting_confirmation = None
                result = run_scenario(scenario_id, state, confirmed=True)
                reply = handle_result(scenario_id, result, state)
            elif answer == "no":
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
