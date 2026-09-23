import hashlib
import re
import time

import streamlit as st

from backend.actions_mock import ActionError, call_action
from backend.confirmation import classify_confirmation
from backend.data_loader import scenario_by_id, slots_catalog
from backend.decision_policy import decide
from backend.executor import run_scenario
from backend.router import route
from backend.language import update_language
from backend.reply_language import localize_details
from backend.speech import speak, transcribe
from backend.slot_normalization import normalize_slot
from backend.state import DialogState
from backend.triage import detect_urgency, normalize_phone

st.set_page_config(page_title="Saqta Voice Router", layout="wide")

st.markdown(
    """
    <style>
    :root {
        --halyk-green: #1E7145;
        --halyk-green-dark: #145032;
        --ink: #16211B;
        --ink-soft: #4B5A52;
        --hairline: #E1E8E3;
        --surface: #F4F8F5;
    }
    .block-container { padding-top: 3.2rem; max-width: 1200px; }
    h1 { font-size: 1.85rem !important; letter-spacing: -0.01em; color: var(--ink); }

    .halyk-banner {
        background: var(--halyk-green-dark);
        color: white;
        border-radius: 12px;
        padding: 26px 32px;
        margin-bottom: 24px;
        display: flex;
        flex-wrap: wrap;
        row-gap: 10px;
        align-items: center;
        justify-content: space-between;
        font-size: 1.15rem;
        font-weight: 500;
        letter-spacing: 0.01em;
        line-height: 1.4;
    }
    .halyk-banner span.tag {
        background: rgba(255,255,255,0.22);
        padding: 6px 16px;
        border-radius: 999px;
        font-weight: 700;
        font-size: 0.95rem;
    }
    .saqta-subtitle { color: var(--ink-soft); font-size: 0.97rem; margin-top: 0.1rem; margin-bottom: 1.4rem; }

    div[data-testid="stChatMessage"] {
        border-radius: 14px;
        border: 1px solid var(--hairline);
        padding: 0.35rem 0.6rem;
        margin-bottom: 4px;
    }

    .trace-card {
        background: var(--surface);
        border: 1px solid var(--hairline);
        border-left: 4px solid var(--halyk-green);
        border-radius: 12px;
        padding: 14px 16px;
        margin-bottom: 10px;
    }
    .trace-empty {
        color: var(--ink-soft);
        font-size: 0.9rem;
        padding: 10px 2px;
    }
    .sc-badge {
        display: inline-block;
        background: var(--halyk-green-dark);
        color: white;
        font-weight: 700;
        font-size: 0.82rem;
        padding: 3px 12px;
        border-radius: 999px;
        margin: 2px 4px 2px 0;
        letter-spacing: 0.01em;
    }
    .sc-badge.alt { background: var(--surface); color: var(--ink-soft); border: 1px solid var(--hairline); }
    .lang-badge {
        display: inline-block;
        border: 1px solid var(--halyk-green);
        color: var(--halyk-green-dark);
        font-weight: 700;
        font-size: 0.75rem;
        padding: 1px 9px;
        border-radius: 6px;
        letter-spacing: 0.04em;
    }
    .conf-bar-track { background: var(--hairline); border-radius: 999px; height: 6px; margin: 6px 0 10px; }
    .conf-bar-fill { background: var(--halyk-green); border-radius: 999px; height: 6px; }
    </style>
    """,
    unsafe_allow_html=True,
)

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

st.markdown(
    '<div class="halyk-banner">'
    '<span>Трек «Коммуникации» · Партнёр задачи — АО «Народный Банк Казахстана»</span>'
    '<span class="tag">HackAlem AI</span>'
    '</div>',
    unsafe_allow_html=True,
)
st.title("Saqta Insurance — Voice Router")
st.markdown(
    '<div class="saqta-subtitle">LLM-слой выбора сценария вместо intent-классификатора · '
    'русский / қазақша / смешанная речь</div>',
    unsafe_allow_html=True,
)
_DEMO_LANG_OPTIONS = {"Русский": "ru", "Қазақша": "kk"}
demo_lang_label = st.radio(
    "Язык по умолчанию для демо",
    options=list(_DEMO_LANG_OPTIONS.keys()),
    horizontal=True,
    key="demo_lang_label",
)
demo_lang = _DEMO_LANG_OPTIONS[demo_lang_label]
if st.session_state.get("_applied_demo_lang") != demo_lang and st.session_state.state.turn == 0:
    st.session_state.state.language = demo_lang
    st.session_state.state.response_language = demo_lang
    st.session_state["_applied_demo_lang"] = demo_lang

st.caption(
    "AI дауысы / Дауыс арқылы жауап береді" if demo_lang == "kk"
    else "AI-голос / Ответы озвучены искусственным голосом"
)

IIN_RE = re.compile(r"\b\d{12}\b")
CLAIM_RE = re.compile(r"\bCL-\d{6}\b", re.IGNORECASE)
POLICY_RE = re.compile(r"\bSQ-(?:OGPO|CASCO|TRVL|PROP|NS|DMS)-\d{6}\b", re.IGNORECASE)


def _interruption(text: str) -> str | None:
    words = set(re.findall(r"\w+", text.lower()))
    if words & {"оператор", "оператором", "человеком", "человека", "адаммен", "операторға"}:
        return "operator"
    if detect_urgency(text):
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


def _friendly_value(value: object, depth: int = 0) -> str:
    if isinstance(value, dict):
        if depth >= 2:
            return "; ".join(str(v) for v in list(value.values())[:3] if not isinstance(v, (dict, list)))
        parts = []
        for key, item in list(value.items())[:5]:
            rendered = _friendly_value(item, depth + 1)
            if rendered:
                parts.append(f"{key.replace('_', ' ')} — {rendered}")
        return "; ".join(parts)
    if isinstance(value, list):
        return "; ".join(_friendly_value(item, depth + 1) for item in value[:6])
    return str(value)


def _system_reply(scenario_id: str, language: str) -> str:
    kk = language == "kk"
    return {
        "SYS_OUT_OF_SCOPE": "Мен тек Saqta Insurance сақтандыру өнімдері мен қызметтері бойынша көмектесе аламын." if kk else "Могу помочь только с продуктами и услугами Saqta Insurance.",
        "SYS_UNCLEAR": "Қай сақтандыру мәселесі бойынша көмектесейін?" if kk else "Уточните, пожалуйста, с каким страховым вопросом вам помочь?",
        "SYS_GOODBYE": "Сау болыңыз!" if kk else "До свидания!",
    }[scenario_id]


def _run_queued(state: DialogState) -> str:
    if not st.session_state.queued_scenarios:
        return ""
    next_id = st.session_state.queued_scenarios.pop(0)
    if next_id.startswith("SYS_"):
        return _system_reply(next_id, state.response_language) + (" " + rest if (rest := _run_queued(state)) else "")
    return handle_result(next_id, run_scenario(next_id, state), state)


def _closing_message(scenario: dict, state: DialogState, executed_actions: list[dict]) -> str:
    merged = dict(state.slots)
    for a in executed_actions:
        if isinstance(a.get("result"), dict):
            merged.update(a["result"])
    merged = {key: _friendly_value(value) if isinstance(value, (dict, list)) else value for key, value in merged.items()}
    lang = state.response_language
    template = scenario.get("responses", {}).get(lang, {}).get("closing")
    if not template:
        return ("Дайын." if lang == "kk" else "Готово.")
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
        return "Операция аяқталды. Толық ақпаратты оператордан біле аласыз." if lang == "kk" else "Операция завершена. Подробности доступны у оператора."


def handle_result(scenario_id: str, result: dict, state: DialogState) -> str:
    status = result.get("status")
    scenario = scenario_by_id(scenario_id) or {}
    kk = state.response_language == "kk"
    st.session_state.turn_actions.extend(result.get("actions", []))

    if status == "need_identification":
        st.session_state.awaiting_identification = scenario_id
        return (
            "Бұл сұрау үшін жеке басыңызды растау қажет. Телефон нөміріңізді немесе ЖСН-іңізді айтыңызшы."
            if kk else
            "Для этой операции нужно вас идентифицировать. Назовите, пожалуйста, номер телефона или ИИН."
        )

    if status == "need_slots":
        st.session_state.awaiting_slots = scenario_id
        missing = result.get("slots", [])
        prompts = {slot["name"]: slot.get("prompt", {}) for slot in slots_catalog()["slots"]}
        language = "kk" if kk else "ru"
        questions = [prompts.get(name, {}).get(language, name) for name in missing]
        opening = scenario.get("responses", {}).get(language, {}).get("opening", "") if scenario_id == "SC11" else ""
        return " ".join(part for part in (opening, *questions) if part)

    if status == "need_confirmation":
        st.session_state.needs_localization = True
        st.session_state.awaiting_slots = None
        st.session_state.awaiting_confirmation = scenario_id
        if kk:
            return f"Тексеріңізші: {result.get('confirmation', 'операция мәліметтері')}. Растайсыз ба? (иә/жоқ)"
        return f"Проверьте, пожалуйста: {result.get('confirmation', 'детали операции')}. Подтверждаете? (да/нет)"

    if status == "confirmation_expired":
        st.session_state.awaiting_confirmation = None
        return "Операция деректері өзгерді. Оларды нақтылап, қайта растауды сұраймын." if kk else "Данные операции изменились. Уточним их и запросим подтверждение заново."

    if status == "action_error":
        if kk:
            return "Әрекетті орындау мүмкін болмады. Операторға қосамын."
        return (
            f"Не получилось выполнить действие «{result.get('action')}» "
            f"({result.get('code')}). Соединяю с оператором."
        )

    if status == "done":
        st.session_state.needs_localization = bool(result.get("actions")) or st.session_state.needs_localization
        st.session_state.awaiting_slots = None
        st.session_state.awaiting_confirmation = None
        reply = _closing_message(scenario, state, result.get("actions", []))
        if st.session_state.queued_scenarios:
            reply += " " + _run_queued(state)
        return reply

    if status == "error":
        if kk:
            return "Сұрауды анықтау мүмкін болмады. Қайта айтып жіберіңізші."
        return f"Не удалось определить сценарий: {result.get('reason')}"

    return "Сұрауды қайта айтып жіберіңізші." if kk else "Повторите, пожалуйста, запрос."


for key, default in {
    "last_audio_hash": None, "last_reply_audio": None, "autoplay_reply": False,
    "stt_error": None, "tts_error": None, "reply_error": None, "failed_audio_hash": None,
    "stt_ms": 0.0, "needs_localization": False,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

chat_col, trace_col = st.columns([2, 1])

with chat_col:
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.write(m["text"])

    if st.session_state.last_reply_audio:
        st.audio(st.session_state.last_reply_audio, format="audio/mp3", autoplay=st.session_state.autoplay_reply)
        st.session_state.autoplay_reply = False
    if st.session_state.reply_error:
        st.warning(st.session_state.reply_error)
    if st.session_state.tts_error:
        st.warning(st.session_state.tts_error)
        if st.button("Қайта тыңдау / Повторить озвучку"):
            try:
                # Retry only synthesis; never execute the scenario a second time.
                reply = st.session_state.messages[-1]["text"]
                audio_bytes, _ = speak(reply, st.session_state.state.response_language)
                st.session_state.last_reply_audio = audio_bytes
                st.session_state.autoplay_reply = True
                st.session_state.tts_error = None
                st.rerun()
            except Exception as error:
                st.caption(type(error).__name__)

    audio_value = st.audio_input("Сөйлеңіз" if demo_lang == "kk" else "Говорите в микрофон")
    typed_input = st.chat_input("...немесе мәтін жазыңыз" if demo_lang == "kk" else "...или напишите текстом")
    retry_stt = False
    if st.session_state.stt_error:
        st.warning(st.session_state.stt_error)
        retry_stt = st.button("Қайта тану / Повторить распознавание")

    user_input = typed_input
    if typed_input and audio_value is not None:
        # Text wins when both inputs arrive; do not replay stale audio on rerun.
        st.session_state.last_audio_hash = hashlib.sha256(audio_value.getvalue()).hexdigest()
        st.session_state.failed_audio_hash = None
        st.session_state.stt_error = None
    st.session_state.stt_ms = 0.0
    if not typed_input and audio_value is not None:
        audio_hash = hashlib.sha256(audio_value.getvalue()).hexdigest()
        new_audio = audio_hash != st.session_state.last_audio_hash
        retry_allowed = audio_hash != st.session_state.failed_audio_hash or retry_stt
        if new_audio and retry_allowed:
            try:
                with st.spinner("Тыңдап жатырмын / Распознаю речь..."):
                    user_input, stt_ms = transcribe(audio_value)
                if not user_input.strip():
                    raise ValueError("No speech recognized")
                st.session_state.stt_ms = stt_ms
                st.session_state.last_audio_hash = audio_hash
                st.session_state.failed_audio_hash = None
                st.session_state.stt_error = None
            except Exception as error:
                user_input = None
                st.session_state.failed_audio_hash = audio_hash
                st.session_state.stt_error = (
                    "Сөйлеуді тану мүмкін болмады. Қайталап көріңіз. / "
                    f"Не удалось распознать речь ({type(error).__name__}). Повторите попытку."
                )
                st.rerun()

    if user_input:
        state = st.session_state.state
        st.session_state.turn_actions = []
        st.session_state.last_reply_audio = None
        st.session_state.messages.append({"role": "user", "text": user_input})
        update_language(state, user_input)
        st.session_state.needs_localization = False
        st.session_state.reply_error = None
        st.session_state.tts_error = None
        router_ms = 0.0
        reply_ms = 0.0

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
        identity_candidate = _extract_identity(user_input) if st.session_state.awaiting_identification else {}
        if st.session_state.awaiting_identification and not identity_candidate and not interruption:
            previous = st.session_state.awaiting_identification
            st.session_state.awaiting_identification = None
            state.scenario_stack.append(previous)
            state.active_scenario = None
        declined_confirmation = bool(interruption == "cancel" and st.session_state.awaiting_confirmation)

        if interruption in ("operator", "cancel", "urgent"):
            previous = (
                st.session_state.awaiting_identification
                or st.session_state.awaiting_confirmation
                or st.session_state.awaiting_slots
            )
            st.session_state.awaiting_identification = None
            st.session_state.awaiting_confirmation = None
            st.session_state.awaiting_slots = None
            if not declined_confirmation:
                st.session_state.queued_scenarios = []
            state.pending_confirmation = None
            if previous:
                state.scenario_stack.append(previous)
                state.active_scenario = None

        if interruption == "operator":
            transfer = call_action("transfer_to_operator", _store=state.mock_data, queue="operator_general")
            st.session_state.turn_actions.append({"action": "transfer_to_operator", "mode": "execute", "result": transfer})
            decision_action = "handoff"
            reply = "Операторға қосып, сұрауыңыз туралы ақпаратты жіберемін." if state.response_language == "kk" else "Соединяю с оператором и передаю контекст вашего обращения."

        elif interruption == "cancel":
            reply = "Жақсы, ағымдағы операцияны тоқтатамын." if state.response_language == "kk" else "Хорошо, отменяю текущую операцию."
            if declined_confirmation and st.session_state.queued_scenarios:
                reply += " " + _run_queued(state)

        elif _sms_retry_requested(user_input, state):
            phone = normalize_phone(user_input) or state.pending_sms.get("phone")
            if not phone:
                state.pending_sms["awaiting_phone"] = True
                reply = "SMS-ті қайта жіберу үшін телефон нөміріңізді айтыңызшы." if state.response_language == "kk" else "Назовите номер телефона для повторной отправки SMS."
            else:
                try:
                    sent = call_action("send_sms", _store=state.mock_data, phone=phone)
                    st.session_state.turn_actions.append({"action": "send_sms", "mode": "execute", "result": sent})
                    state.pending_sms = None
                    reply = "SMS қайта жіберілді." if state.response_language == "kk" else "SMS отправлена повторно."
                except ActionError as error:
                    state.pending_sms["phone"] = phone
                    reply = "SMS жіберу мүмкін болмады. Негізгі операция орындалды." if state.response_language == "kk" else f"Не удалось отправить SMS ({error.code}). Основная операция уже выполнена."

        elif st.session_state.awaiting_identification:
            scenario_id = st.session_state.awaiting_identification
            ident = identity_candidate
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
                reply = (
                    "Бұл деректер бойынша клиент табылмады. Телефон нөмірін немесе ЖСН-ді қайта айтыңызшы."
                    if state.response_language == "kk" else
                    "Не нашла клиента с такими данными. Повторите телефон или ИИН, пожалуйста."
                )

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
                reply = "Жақсы, операцияны тоқтатамын." if state.response_language == "kk" else "Хорошо, отменяю операцию."
                if st.session_state.queued_scenarios:
                    reply += " " + _run_queued(state)
            else:
                reply = "Растаңызшы (иә/жоқ)." if state.response_language == "kk" else "Пожалуйста, подтвердите (да/нет)."

        else:
            if st.session_state.awaiting_slots:
                state.active_scenario = st.session_state.awaiting_slots
            route_started = time.perf_counter()
            try:
                router_output = route(user_input, state)
            except (ValueError, KeyError, TypeError):
                router_output = {
                    "scenarios": [{"scenario_id": "SYS_UNCLEAR", "confidence": 1.0, "reason": "router validation failed"}],
                    "alternatives": [], "slots": {}, "is_continuation": False,
                }
            router_ms = (time.perf_counter() - route_started) * 1000
            update_language(state, user_input, router_output.get("language"), router_output.get("response_language"))
            for slot_name, slot_value in router_output.get("slots", {}).items():
                normalized = normalize_slot(slot_name, slot_value)
                if normalized is not None:
                    state.set_slot(slot_name, normalized)

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
                if is_slot_continuation:
                    for extra_id in decision.scenario_ids:
                        if extra_id != scenario_id and extra_id not in st.session_state.queued_scenarios:
                            st.session_state.queued_scenarios.append(extra_id)
                else:
                    st.session_state.queued_scenarios = decision.scenario_ids[1:]
                if scenario_id.startswith("SYS_"):
                    reply = _system_reply(scenario_id, state.response_language)
                    if st.session_state.queued_scenarios:
                        reply += " " + _run_queued(state)
                else:
                    result = run_scenario(scenario_id, state)
                    reply = handle_result(scenario_id, result, state)
                st.session_state.low_confidence_streak = 0
            elif decision.action == "clarify":
                prefix = "Нақты не қызықтыратынын айтыңызшы: " if state.response_language == "kk" else "Уточните, пожалуйста, что именно вас интересует: "
                reply = prefix + ", ".join((scenario_by_id(sid) or {}).get("description", sid) for sid in decision.clarify_options)
                st.session_state.needs_localization = True
                st.session_state.low_confidence_streak += 1
            else:
                reply = "Сізді операторға қосамын." if state.response_language == "kk" else "Соединяю вас с оператором."
                st.session_state.low_confidence_streak = 0

        dialog_ms = (time.perf_counter() - t0) * 1000 - router_ms
        if st.session_state.needs_localization:
            reply_started = time.perf_counter()
            try:
                reply = localize_details(reply, state.response_language)
            except Exception:
                st.session_state.reply_error = (
                    "Аударма уақытша қолжетімсіз. Бастапқы жауап көрсетілді. / "
                    "Перевод временно недоступен; показан исходный ответ."
                )
            reply_ms = (time.perf_counter() - reply_started) * 1000
        tts_started = time.perf_counter()
        try:
            audio_bytes, tts_ms = speak(reply, state.response_language)
            st.session_state.last_reply_audio = audio_bytes
            st.session_state.autoplay_reply = True
        except Exception as error:
            tts_ms = (time.perf_counter() - tts_started) * 1000
            st.session_state.last_reply_audio = None
            st.session_state.tts_error = (
                "Дауыстық жауап қолжетімсіз. Қайта тыңдап көріңіз. / "
                f"Озвучка не удалась ({type(error).__name__}); ответ сохранён."
            )
        t_total = (time.perf_counter() - t0) * 1000 + st.session_state.stt_ms

        state.record_turn("client", user_input)
        state.record_turn("bot", reply)
        st.session_state.messages.append({"role": "assistant", "text": reply})

        st.session_state.last_trace = {
            "turn": state.turn,
            "transcript": user_input,
            "language": state.language,
            "response_language": state.response_language,
            "audio_errors": {"tts": st.session_state.tts_error, "reply": st.session_state.reply_error},
            "scenarios": (router_output or {}).get("scenarios", []),
            "alternatives": (router_output or {}).get("alternatives", []),
            "reason": [s.get("reason") for s in (router_output or {}).get("scenarios", [])],
            "decision": decision_action,
            "slots": state.slots,
            "actions": st.session_state.turn_actions,
            "latency_ms": {
                "stt": round(st.session_state.stt_ms, 1),
                "router": round(router_ms, 1),
                "dialog": round(dialog_ms, 1),
                "reply": round(reply_ms, 1),
                "tts": round(tts_ms, 1),
                "total": round(t_total, 1),
            },
        }
        st.rerun()

with trace_col:
    st.subheader("Trace panel")
    st.caption(
        "Роутер не шешкенін супервизор осы жерден көреді" if demo_lang == "kk"
        else "Что решил роутер и почему — видно супервизору после каждой реплики"
    )

    trace = st.session_state.get("last_trace", {})

    if not trace:
        st.markdown(
            '<div class="trace-empty">Пока нет ни одной реплики — скажите что-нибудь клиенту слева.</div>',
            unsafe_allow_html=True,
        )
    else:
        lang_label = {"ru": "RU", "kk": "KK", "mixed": "RU+KK"}.get(
            trace.get("language"), (trace.get("language") or "—").upper()
        )
        st.markdown(
            f"**Реплика #{trace.get('turn', '—')}** &nbsp; "
            f'<span class="lang-badge">{lang_label}</span>',
            unsafe_allow_html=True,
        )
        st.caption("Язык ответа: " + str(trace.get("response_language", "—")))
        if trace.get("transcript"):
            st.markdown(f"> {trace['transcript']}")

        scenarios = trace.get("scenarios") or []
        if scenarios:
            badges = "".join(
                f'<span class="sc-badge">{s.get("scenario_id")} '
                f'({round((s.get("confidence") or 0) * 100)}%)</span>'
                for s in scenarios
            )
            st.markdown(f"**Выбрано:** {badges}", unsafe_allow_html=True)
            top_conf = round((scenarios[0].get("confidence") or 0) * 100)
            st.markdown(
                f'<div class="conf-bar-track"><div class="conf-bar-fill" '
                f'style="width:{top_conf}%"></div></div>',
                unsafe_allow_html=True,
            )
            reasons = [r for r in trace.get("reason", []) if r]
            if reasons:
                st.caption("Почему: " + " · ".join(reasons))

        alternatives = trace.get("alternatives") or []
        if alternatives:
            alt_badges = "".join(
                f'<span class="sc-badge alt">{a.get("scenario_id")} '
                f'({round((a.get("confidence") or 0) * 100)}%)</span>'
                for a in alternatives
            )
            st.markdown(f"**Альтернативы:** {alt_badges}", unsafe_allow_html=True)

        latency = trace.get("latency_ms") or {}
        if latency:
            cols = st.columns(len(latency))
            for col, (stage, ms) in zip(cols, latency.items()):
                col.metric(stage.replace("_", " ").upper(), f"{ms:.0f} мс")

        if trace.get("slots"):
            with st.expander("Слоты"):
                st.json(trace["slots"])
        if trace.get("actions"):
            with st.expander("Выполненные действия"):
                st.json(trace["actions"])

        with st.expander("Сырой trace (JSON)"):
            st.json(trace)
