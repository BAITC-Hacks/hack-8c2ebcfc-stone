import re


def detect_language(text: str) -> str:
    cyrillic = sum(1 for c in text if "Ѐ" <= c <= "ӿ")
    latin = sum(1 for c in text if "a" <= c.lower() <= "z")
    kazakh_specific = set("ӘәҒғҚқҢңӨөҰұҮүҺһІі")
    has_kk = any(c in kazakh_specific for c in text)

    if cyrillic == 0 and latin > 0:
        return "ru"
    if has_kk:
        return "kk" if latin < 2 else "mixed"
    if cyrillic > 0 and latin > 2:
        return "mixed"
    return "ru" if cyrillic > 0 else "ru"


PHONE_RE = re.compile(r"(?:\+?7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")


def normalize_phone(text: str) -> str | None:
    m = PHONE_RE.search(text)
    if not m:
        return None
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
