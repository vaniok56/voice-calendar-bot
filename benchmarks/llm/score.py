"""Field-level scoring for the extraction benchmark.

Scores classification exactly, spans as exact/partial, and flags invented spans
(output present where expected null) and ungrounded spans (output that is not a
substring of the source text). Accepts null and empty string as "absent".
"""

import unicodedata

import contract


def normalize(text: str) -> str:
    text = text.casefold()
    text = "".join(
        " " if unicodedata.category(char).startswith(("P", "S")) else char
        for char in text
    )
    return " ".join(text.split())


def field_status(expected, actual) -> str:
    want = normalize(expected or "")
    have = normalize(actual or "")
    if not want and not have:
        return "ok"
    if want and not have:
        return "missing"
    if not want and have:
        return "invented"
    if want == have:
        return "exact"
    if want in have or have in want:
        return "partial"
    return "wrong"


def _list_status(expected: list, actual: list) -> str:
    want = [normalize(item) for item in expected]
    have = [normalize(item) for item in actual]
    if not want and not have:
        return "ok"
    if want and not have:
        return "missing"
    if not want and have:
        return "invented"
    if want == have:
        return "exact"
    if set(want) & set(have):
        return "partial"
    return "wrong"


def grounded(span, source: str) -> bool:
    return not (span or "").strip() or normalize(span) in normalize(source)


def score(expected: dict, actual: dict, source: str) -> dict:
    result = {
        field: ("ok" if expected.get(field) == actual.get(field) else "wrong")
        for field in contract.CLASSIFY_FIELDS
    }
    for field in contract.SPAN_FIELDS:
        result[field] = field_status(expected.get(field), actual.get(field))
    result["reminder_texts"] = _list_status(
        expected.get("reminder_texts", []), actual.get("reminder_texts", [])
    )
    result["ungrounded"] = [
        actual.get(field)
        for field in contract.SPAN_FIELDS
        if not grounded(actual.get(field), source)
    ] + [item for item in actual.get("reminder_texts", []) if not grounded(item, source)]
    return result
