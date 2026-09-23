import re


# Compatibility import; language detection never selects a scenario.
from backend.language import detect_language


PHONE_RE = re.compile(r"(?<!\d)(?:\+?7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)")

_UNITS = {
    "ноль": 0, "нуль": 0, "нөл": 0, "один": 1, "одна": 1, "бір": 1,
    "два": 2, "екі": 2, "три": 3, "үш": 3, "четыре": 4, "төрт": 4,
    "пять": 5, "бес": 5, "шесть": 6, "алты": 6, "семь": 7, "жеті": 7,
    "восемь": 8, "сегіз": 8, "девять": 9, "тоғыз": 9,
}
_TENS = {
    "десять": 10, "он": 10, "двадцать": 20, "жиырма": 20,
    "тридцать": 30, "отыз": 30, "сорок": 40, "қырық": 40,
    "пятьдесят": 50, "елу": 50, "шестьдесят": 60, "алпыс": 60,
    "семьдесят": 70, "жетпіс": 70, "восемьдесят": 80, "сексен": 80,
    "девяносто": 90, "тоқсан": 90,
}
_HUNDREDS = {
    "сто": 100, "жүз": 100, "двести": 200, "екі жүз": 200,
    "триста": 300, "четыреста": 400, "пятьсот": 500,
    "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
}


def _spoken_phone(text: str) -> str | None:
    match = re.search(r"(?:плюс|\+)", text.lower())
    if not match:
        return None
    phrase = text[match.end():].lower()
    groups = re.split(r"[,.;:]", phrase)
    digits = ""
    for group in groups:
        words = re.findall(r"[а-яәғқңөұүһі]+|\d+", group)
        numbers = [w for w in words if w in _UNITS or w in _TENS or w in _HUNDREDS or w.isdigit()]
        if not numbers:
            continue
        if not digits and numbers[0] in ("семь", "жеті", "7"):
            digits = "7"
            numbers = numbers[1:]
        if not numbers:
            continue
        if all(w in _UNITS or (w.isdigit() and len(w) == 1) for w in numbers):
            digits += "".join(str(_UNITS[w] if w in _UNITS else w) for w in numbers)
        else:
            total = 0
            pending = 0
            for word in numbers:
                if word.isdigit():
                    total += int(word)
                elif word == "жүз":
                    total += max(1, pending) * 100
                    pending = 0
                elif word in _HUNDREDS:
                    total += _HUNDREDS[word]
                elif word in _TENS:
                    total += _TENS[word]
                else:
                    pending += _UNITS[word]
            total += pending
            digits += str(total)
        if len(digits) >= 11:
            break
    return "+" + digits if re.fullmatch(r"7\d{10}", digits) else None


def normalize_phone(text: str) -> str | None:
    m = PHONE_RE.search(text)
    if not m:
        return _spoken_phone(text)
    digits = re.sub(r"\D", "", m.group())
    if digits.startswith("8"):
        digits = "7" + digits[1:]
    return "+" + digits


URGENCY_PATTERNS = {
    "accident_now": [
        r"авари[яию]",
        r"столкнул",
        r"дтп",
        r"жол апаты",
    ],
    "abroad_illness": [
        r"за границ",
        r"в другой стран",
        r"шетелде",
        r"заболел.*(поездк|отпуск|границ)",
    ],
    "fraud": [
        r"мошенник",
        r"подозрительн.*звонок",
        r"алаяқ",
    ],
}


def detect_urgency(text: str) -> str | None:
    t = text.lower()
    for label, patterns in URGENCY_PATTERNS.items():
        if any(re.search(p, t, re.IGNORECASE) for p in patterns):
            return label
    return None


def split_multi_intent(text: str) -> list[str]:
    parts = re.split(r"(?:,\s*(?:и ещё|и еще|а ещё|а еще|ещё)|;\s*)", text)
    return [p.strip() for p in parts if p.strip()]
