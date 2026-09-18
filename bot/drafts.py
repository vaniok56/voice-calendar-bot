"""In-memory event drafts awaiting a missing field from the user.

One draft per user. The bot asks for the first missing field, keeps the rest of
the extracted schema, and folds the next reply into that field.
"""

from dataclasses import dataclass


@dataclass
class Draft:
    raw: dict
    awaiting: str
    chat_id: int | None = None
    message_id: int | None = None


def next_field(resolved: dict) -> str | None:
    return (resolved.get("missing") or [None])[0]


def apply_answer(draft: Draft, text: str, resolve_fn) -> dict:
    draft.raw[draft.awaiting] = text
    resolved = resolve_fn(draft.raw)
    following = next_field(resolved)
    if following is not None:
        draft.awaiting = following
    return resolved
