"""Conversation language selection, independent of scenario routing.

Strong language evidence takes precedence over the router's language hints.
The router resolves utterances with sparse local evidence. The same small fallback
serves confirmation/identification turns that do not call the router and keeps
language stable for language-neutral values.
"""

import re

_LANGUAGES = {"ru", "kk", "mixed"}
_KAZAKH_LETTERS = frozenset("әғқңөұүһі")
_SHARED_TERMS = {
    "sms", "смс", "огпо", "каско", "дмс", "ogpo", "casco", "dms",
    "iin", "иин", "жсн", "otp", "cvv", "cvc", "pdf", "email", "e-mail",
    "полис", "онлайн", "insurance", "saqta",
}
_KAZAKH_WORDS = {
    "мен", "сен", "біз", "сіз", "бұл", "осы", "сол", "керек", "бар",
    "жоқ", "бола", "болады", "болды", "болса", "болмайды", "алсам",
    "алғым", "келеді", "аламын", "алам", "алу", "бер", "беру", "беремін",
    "ма", "ме", "бе", "ба", "па", "пе", "үшін", "бойынша", "сәлем",
    "саламатсыз", "сәлеметсіз", "рақмет", "рахмет", "иә", "растаймын",
}
_RUSSIAN_WORDS = {
    "здравствуйте", "здравствуй", "привет", "спасибо", "пожалуйста",
    "добрый", "день", "будет", "стоить",
    "хочу", "хотим", "нужен", "нужна", "нужно", "нужны", "можно",
    "можете", "можно", "как", "какой", "какая", "какие", "что", "где",
    "когда", "сколько", "почему", "мне", "меня", "мой", "моя", "мои",
    # "да" and "не" are also Kazakh particles/words, so neither is evidence
    # of Russian inside an otherwise Kazakh utterance.
    "вас", "вам", "вы", "это", "есть", "нет", "для", "по",
    "при", "после", "сейчас", "сегодня", "завтра", "ещё", "еще",
    "подтверждаю", "отменяю", "отмена", "верно", "хорошо", "понятно",
    "повторите", "назовите", "скажите", "соедините", "оформить", "купить",
    "страховка", "страховку", "страхование", "страхования", "оплатить",
    "приходит", "действует", "ли", "надо",
    "русском", "русский", "русски", "в", "на", "и", "с", "у", "я",
}


def _words(text: str) -> list[str]:
    text = text.casefold()
    # Values such as policy IDs, email addresses and phone numbers do not tell
    # us which language the caller wants to hear next.
    text = re.sub(r"https?://\S+|\b[^\s@]+@[^\s@]+\b", " ", text)
    text = re.sub(r"\b[\w-]*\d[\w-]*\b", " ", text)
    return [word for word in re.findall(r"[^\W\d_]+", text) if word not in _SHARED_TERMS]


def _has_language_content(text: str) -> bool:
    return any(any("а" <= char <= "я" or char == "ё" or char in _KAZAKH_LETTERS
                   for char in word) for word in _words(text))


def _language_counts(words: list[str]) -> tuple[int, int]:
    """Count language-bearing words, leaving shared domain terms unweighted."""
    kazakh = sum(
        word in _KAZAKH_WORDS or any(char in _KAZAKH_LETTERS for char in word)
        for word in words
    )
    russian = sum(word in _RUSSIAN_WORDS for word in words)
    return russian, kazakh


def detect_language(text: str, previous: str = "ru") -> str:
    """Estimate RU/KK/code-switching; retain prior language for neutral values."""
    previous = previous if previous in _LANGUAGES else "ru"
    words = _words(text)
    if not _has_language_content(text):
        return previous
    russian, kazakh = _language_counts(words)
    if kazakh and russian:
        return "mixed"
    if kazakh:
        return "kk"
    # Without positive evidence, RU is only a fallback that the LLM can override.
    return "ru"


def update_language(
    state,
    text: str,
    router_language: str | None = None,
    response_language: str | None = None,
) -> None:
    """Update detected and reply languages without switching on IDs/numbers."""
    previous = getattr(state, "language", "ru")
    if previous not in _LANGUAGES:
        previous = "ru"
    previous_response = getattr(state, "response_language", None)
    if previous_response not in {"ru", "kk"}:
        previous_response = "kk" if previous in {"kk", "mixed"} else "ru"
    if not _has_language_content(text):
        state.language = previous
        state.response_language = previous_response
        return
    local_language = detect_language(text, previous)
    words = _words(text)
    russian, kazakh = _language_counts(words)
    detected = (
        router_language if router_language in _LANGUAGES
        else local_language
    )
    # A mistaken router label must not change a clearly Russian/Kazakh turn
    # into the other reply language (including after a previous language switch).
    # Only positive evidence is authoritative: no Kazakh-specific letters does
    # not prove Russian, e.g. "Полис жасату" still needs the router's decision.
    # A small lexicon can miss part of mixed speech. One known word, or half
    # the words, is insufficient to overrule a mixed label from the model.
    strong_single_language = max(russian, kazakh) >= 2 and max(russian, kazakh) * 2 > len(words)
    if local_language == "mixed" or strong_single_language:
        detected = local_language
    state.language = detected
    if detected != "mixed":
        state.response_language = detected
    elif response_language in {"ru", "kk"}:
        state.response_language = response_language
    else:
        state.response_language = (
            "kk" if kazakh > russian else "ru" if russian > kazakh
            else previous_response
        )
