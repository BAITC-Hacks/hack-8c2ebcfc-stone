import time

import streamlit as st

from backend.decision_policy import decide
from backend.executor import run_scenario
from backend.router import route
from backend.state import DialogState
from backend.triage import detect_language

st.set_page_config(page_title="Saqta Voice Router", layout="wide")

if "state" not in st.session_state:
    st.session_state.state = DialogState()
if "messages" not in st.session_state:
    st.session_state.messages = []
if "low_confidence_streak" not in st.session_state:
    st.session_state.low_confidence_streak = 0

st.title("Saqta Insurance — Voice Router")

chat_col, trace_col = st.columns([2, 1])

with chat_col:
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.write(m["text"])

    user_input = st.chat_input("Скажите что-нибудь клиенту...")

    if user_input:
        state = st.session_state.state
        st.session_state.messages.append({"role": "user", "text": user_input})

        t0 = time.perf_counter()
        state.language = detect_language(user_input)
        router_output = route(user_input, state)
        t_router = time.perf_counter() - t0

        decision = decide(router_output, st.session_state.low_confidence_streak)

        if decision.action == "run":
            result = run_scenario(decision.scenario_ids[0], state)
            reply = f"Обрабатываю сценарий {decision.scenario_ids[0]}: {result}"
            st.session_state.low_confidence_streak = 0
        elif decision.action == "clarify":
            reply = f"Уточните, пожалуйста: {decision.clarify_options}"
            st.session_state.low_confidence_streak += 1
        else:
            reply = "Соединяю вас с оператором."
            st.session_state.low_confidence_streak = 0

        state.record_turn("client", user_input)
        state.record_turn("bot", reply)
        st.session_state.messages.append({"role": "assistant", "text": reply})

        st.session_state.last_trace = {
            "turn": state.turn,
            "transcript": user_input,
            "language": state.language,
            "scenarios": router_output.get("scenarios", []),
            "alternatives": router_output.get("alternatives", []),
            "decision": decision.action,
            "slots": state.slots,
            "latency_ms": {"router": round(t_router * 1000, 1)},
        }
        st.rerun()

with trace_col:
    st.subheader("Trace panel")
    st.json(st.session_state.get("last_trace", {}))
