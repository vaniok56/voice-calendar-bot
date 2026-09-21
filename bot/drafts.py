"""In-memory semantic drafts awaiting strict structured answers."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import semantic


@dataclass
class Draft:
    raw: dict
    evidence: list[str]
    reference: datetime
    awaiting: str
    record_path: Path
    attempts: int = 0
    chat_id: int | None = None
    message_id: int | None = None


def next_field(resolved: dict) -> str | None:
    return (resolved.get("missing") or [None])[0]


def apply_answer(draft: Draft, text: str) -> dict:
    value = text.strip()
    if draft.awaiting == "title":
        if not value:
            raise ValueError("Title must not be empty.")
        draft.raw["title"] = value
    elif draft.awaiting == "date":
        try:
            day = date.fromisoformat(value)
        except ValueError as error:
            raise ValueError("Use YYYY-MM-DD or a date button.") from error
        draft.raw["date"] = {
            "kind": "absolute", "source": value, "year": day.year,
            "month": day.month, "day": day.day, "weekday": None,
            "offset_years": 0, "offset_months": 0,
            "offset_weeks": 0, "offset_days": 0,
        }
    elif draft.awaiting == "time":
        if value.casefold() == "all day":
            draft.raw["all_day"] = True
            draft.raw["time"] = semantic._none_time()
        else:
            match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value)
            if match is None:
                raise ValueError("Use HH:MM (24-hour time) or a time button.")
            draft.raw["time"] = {
                "kind": "clock", "source": value,
                "hour": int(match.group(1)), "minute": int(match.group(2)),
                "meridiem": "24h", "offset_hours": 0,
                "offset_minutes": 0, "approximate": False,
            }
    else:
        raise ValueError("Unsupported clarification field.")

    draft.evidence.append(value)
    draft.attempts += 1
    resolved = semantic.resolve(
        draft.raw, "\n".join(draft.evidence), draft.reference
    )
    following = next_field(resolved)
    if following is not None:
        draft.awaiting = following
    return resolved
