import re

_YES = {"да", "ага", "угу", "подтверждаю", "ок", "окей", "конечно", "хорошо", "верно", "yes", "ok", "иә", "растаймын", "дұрыс"}
_NO = {"нет", "не", "надо", "отмена", "отменить", "отменяю", "no", "cancel", "жоқ"}


def classify_confirmation(text: str) -> str:
    """Return yes, no, or unclear; only an unambiguous yes may commit."""
    words = set(re.findall(r"\w+", text.lower()))
    yes = bool(words & _YES)
    no = bool(words & _NO)
    if "не" in words:
        return "no"
    if yes and no:
        return "unclear"
    if no:
        return "no"
    return "yes" if yes else "unclear"
