"""Deterministic resolver: verbatim spans -> canonical event payload.

Port of benchmarks/llm/resolve.py with the benchmark scoring removed. Relative
spans resolve against a reference date (today in Chisinau by default). The
start is timezone-aware, ambiguity in the clock is surfaced, and a missing
duration falls back to an event-type default.
"""

import re
import unicodedata
from datetime import date, datetime, timedelta

from .logging_config import CHISINAU

DEFAULT_DURATION = {
    "meeting": 60,
    "appointment": 30,
    "class": 90,
    "exam": 120,
    "call": 30,
    "reminder": 15,
    "task": 30,
    "other": 60,
}

NUM = {
    "un": 1, "o": 1, "una": 1, "unu": 1, "doi": 2, "doua": 2, "trei": 3,
    "patru": 4, "cinci": 5, "sase": 6, "sapte": 7, "opt": 8, "noua": 9, "zece": 10,
    "unsprezece": 11, "doisprezece": 12,
    "один": 1, "одна": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12,
    "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
TENS = {
    "douazeci": 20, "treizeci": 30, "patruzeci": 40, "cincizeci": 50, "saizeci": 60,
    "saptezeci": 70, "optzeci": 80, "nouazeci": 90,
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
ORD = {
    "первого": 1, "второго": 2, "третьего": 3, "четвертого": 4, "пятого": 5,
    "шестого": 6, "седьмого": 7, "восьмого": 8, "девятого": 9, "десятого": 10,
    "одиннадцатого": 11, "двенадцатого": 12,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
}
REL = {
    "azi": 0, "astazi": 0, "maine": 1, "poimaine": 2, "ieri": -1,
    "сегодня": 0, "завтра": 1, "послезавтра": 2, "вчера": -1,
    "today": 0, "tomorrow": 1, "yesterday": -1,
}
WEEKDAYS = {
    "luni": 0, "marti": 1, "miercuri": 2, "joi": 3, "vineri": 4, "sambata": 5,
    "duminica": 6,
    "понедельник": 0, "вторник": 1, "среда": 2, "среду": 2, "четверг": 3,
    "пятница": 4, "пятницу": 4, "суббота": 5, "субботу": 5, "воскресенье": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
    "saturday": 5, "sunday": 6,
}
MONTHS = {
    "ianuarie": 1, "februarie": 2, "martie": 3, "aprilie": 4, "mai": 5, "iunie": 6,
    "iulie": 7, "august": 8, "septembrie": 9, "octombrie": 10, "noiembrie": 11,
    "decembrie": 12,
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
MINUTE_UNITS = {"minut", "minute", "min", "минут", "минута", "минуты", "минуту", "minutes"}
HOUR_UNITS = {"ora", "ore", "час", "часа", "часов", "hour", "hours", "h"}
DAY_UNITS = {"zi", "zile", "ziua", "день", "дня", "дней", "day", "days"}
ARTICLES = {"un", "o", "una", "unu", "a", "an"}

# (pattern, number table, hour offset, minute)
_TIME_FORMS = (
    (r"fara\s+(?:un\s+)?sfert(?:\s+de)?\s+([\w-]+)", NUM, -1, 45),
    (r"([\w-]+)\s+si\s+(?:un\s+)?sfert", NUM, 0, 15),
    (r"([\w-]+)\s+si\s+jumat", NUM, 0, 30),
    (r"половин\w*\s+([\w-]+)", ORD, -1, 30),
    (r"без\s+четверти\s+([\w-]+)", NUM, -1, 45),
    (r"half past\s+([\w-]+)", NUM, 0, 30),
    (r"quarter past\s+([\w-]+)", NUM, 0, 15),
    (r"quarter to\s+([\w-]+)", NUM, -1, 45),
)


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    text = re.sub(r"[^\w\s:.-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _token_number(token: str):
    token = token.strip(".")
    if not token:
        return None
    if token.isdigit():
        return int(token)
    total = 0
    found = False
    for part in re.split(r"[- ]", token):
        if part.isdigit():
            total += int(part)
            found = True
        elif part in NUM:
            total += NUM[part]
            found = True
        elif part in TENS:
            total += TENS[part]
            found = True
    return total if found else None


def _apply_meridiem(hour: int, minute: int, meridiem):
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return hour % 24, minute


def _meridiem(body: str):
    if re.search(r"\b(am|dimineata|morning|утра|утром)\b", body):
        return "am"
    if re.search(
        r"\b(pm|seara|evening|вечера|вечером|dupa-?amiaza|afternoon|noaptea|night|ночи)\b",
        body,
    ):
        return "pm"
    return None


def parse_time(text: str):
    body = fold(text)
    if not body:
        return None
    meridiem = _meridiem(body)

    if re.search(r"\b(полдень|amiaz\w*|noon)\b", body) and not re.search(r"\d", body):
        return _apply_meridiem(12, 0, meridiem)

    match = re.search(r"(\d{1,2}):(\d{2})", body)
    if match:
        return _apply_meridiem(int(match.group(1)), int(match.group(2)), meridiem)

    for pattern, table, offset, minute in _TIME_FORMS:
        match = re.search(pattern, body)
        if match and match.group(1) in table:
            return _apply_meridiem(table[match.group(1)] + offset, minute, meridiem)

    tokens = body.split()
    for index in range(len(tokens) - 1):
        first = _token_number(tokens[index])
        second = _token_number(tokens[index + 1])
        if first and second and 1 <= first <= 12 and 1 <= second <= 59:
            return _apply_meridiem(first, second, meridiem)

    for token in tokens:
        if token in ARTICLES:
            continue
        value = _token_number(token)
        if value is not None and 1 <= value <= 23:
            return _apply_meridiem(value, 0, meridiem)
    return None


def parse_date(text: str, reference: date):
    body = fold(text)
    if not body:
        return None
    tokens = body.split()
    for token in tokens:
        if token in REL:
            return reference + timedelta(days=REL[token])
    for token in tokens:
        if token in WEEKDAYS:
            delta = (WEEKDAYS[token] - reference.weekday()) % 7 or 7
            return reference + timedelta(days=delta)
    day = month = None
    for token in tokens:
        if token in MONTHS:
            month = MONTHS[token]
        elif token.isdigit() and 1 <= int(token) <= 31:
            day = int(token)
        elif token in ORD and 1 <= ORD[token] <= 31:
            day = ORD[token]
    if day and month:
        result = date(reference.year, month, day)
        if result < reference:
            result = date(reference.year + 1, month, day)
        return result
    return None


def _parse_offset(text: str):
    body = fold(text)
    if not body:
        return None
    if re.search(r"\b(полтора\s+часа|one\s+and\s+a\s+half\s+hours?|o\s+ora\s+si\s+jumatate)\b", body):
        return 90
    if re.search(r"\b(полчаса|пол\s+часа|half\s+(an\s+)?hour|jumatate\s+de\s+ora)\b", body):
        return 30
    if re.search(r"\b(quarter\s+(of\s+an\s+)?hour|четверть\s+часа|sfert\s+de\s+ora)\b", body):
        return 15
    unit = None
    for token in body.split():
        if token in MINUTE_UNITS:
            unit = 1
        elif token in HOUR_UNITS:
            unit = 60
        elif token in DAY_UNITS:
            unit = 1440
    if unit is None:
        return None
    number = None
    for token in body.split():
        value = _token_number(token)
        if value is not None:
            number = value
            break
    return (number or 1) * unit


def parse_recurrence(text: str):
    body = fold(text)
    if not body:
        return None
    for token in body.split():
        if token in WEEKDAYS:
            return {"freq": "weekly", "weekday": WEEKDAYS[token]}
    return None


def resolve(extraction: dict, reference: date | None = None) -> dict:
    reference = reference or datetime.now(CHISINAU).date()
    operation = extraction.get("operation")
    event_type = extraction.get("event_type")
    date_text = extraction.get("date_text")
    time_text = extraction.get("time_text")
    duration_text = extraction.get("duration_text")
    end_text = extraction.get("end_time_text")
    reminders = extraction.get("reminder_texts") or []

    day = parse_date(date_text, reference) if date_text else None
    clock = parse_time(time_text) if time_text else None
    duration = _parse_offset(duration_text) if duration_text else None
    end_clock = parse_time(end_text) if end_text else None
    recurrence_text = extraction.get("recurrence_text")
    recurrence = parse_recurrence(recurrence_text) if recurrence_text else None
    if day is None and recurrence:
        weekday = recurrence["weekday"]
        day = reference + timedelta(days=(weekday - reference.weekday()) % 7 or 7)
    offsets = [value for value in (_parse_offset(item) for item in reminders) if value is not None]

    ambiguous = bool(
        clock and ":" not in time_text and _meridiem(fold(time_text)) is None and clock[0] <= 12
    )

    all_day = event_type == "birthday"
    start = None
    if day and clock:
        start = datetime(day.year, day.month, day.day, clock[0], clock[1])
    if end_clock and start:
        end = datetime(start.year, start.month, start.day, end_clock[0], end_clock[1])
        if end < start:
            end += timedelta(days=1)
        if duration is None:
            duration = int((end - start).total_seconds() // 60)

    unresolved = []
    if date_text and day is None:
        unresolved.append("date_text")
    if time_text and clock is None:
        unresolved.append("time_text")
    if duration_text and duration is None:
        unresolved.append("duration_text")
    if end_text and end_clock is None:
        unresolved.append("end_time_text")
    if any(_parse_offset(item) is None for item in reminders):
        unresolved.append("reminder_texts")
    if recurrence_text and recurrence is None:
        unresolved.append("recurrence_text")

    if duration is None and start is not None and not all_day:
        duration = DEFAULT_DURATION.get(event_type)

    missing = []
    if operation == "create":
        if not extraction.get("title"):
            missing.append("title")
        if not all_day and day is None:
            missing.append("date_text")
        if not all_day and clock is None:
            missing.append("time_text")

    return {
        "operation": operation,
        "event_type": event_type,
        "title": extraction.get("title"),
        "start": start.replace(tzinfo=CHISINAU).isoformat() if start else None,
        "all_day": all_day,
        "duration_minutes": duration,
        "location": extraction.get("location_text"),
        "recurrence": recurrence,
        "reminders_minutes": sorted(set(offsets)),
        "ambiguous": ambiguous,
        "complete": all_day or start is not None,
        "missing": missing,
        "unresolved": unresolved,
    }
