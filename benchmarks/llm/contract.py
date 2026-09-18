"""Extraction contract: enums, flat JSON schema, prompt, coercion.

Span fields are nullable strings. null means the message does not state the
field. reminder_texts is a string array. The schema stays flat (no $ref/$defs),
which some structured-output backends require.
"""

OPERATIONS = ["create", "edit", "delete", "list"]
EVENT_TYPES = [
    "meeting", "appointment", "class", "exam", "call",
    "trip", "birthday", "reminder", "task", "other",
]
SPAN_FIELDS = [
    "title", "date_text", "time_text", "duration_text",
    "end_time_text", "location_text", "recurrence_text",
]
CLASSIFY_FIELDS = ["operation", "event_type"]
FIELDS = [*CLASSIFY_FIELDS, *SPAN_FIELDS, "reminder_texts"]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": FIELDS,
    "properties": {
        "operation": {"type": "string", "enum": OPERATIONS},
        "event_type": {"type": "string", "enum": EVENT_TYPES},
        **{
            field: {"type": ["string", "null"], "description": "verbatim span or null"}
            for field in SPAN_FIELDS
        },
        "reminder_texts": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM_PROMPT = (
    "Extract one calendar event from the user message. Reply with a single JSON "
    "object and nothing else. operation is one of "
    + ", ".join(OPERATIONS)
    + ". event_type is one of "
    + ", ".join(EVENT_TYPES)
    + ". Copy these spans verbatim from the message, in the original language: "
    + ", ".join(SPAN_FIELDS)
    + ". Use null for a span the message does not state. reminder_texts is a list "
    "of verbatim reminder phrases. Never translate, normalize, reformat, or invent."
)


def build_messages(text: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]


def empty_extraction() -> dict:
    record = {"operation": "list", "event_type": "other"}
    for field in SPAN_FIELDS:
        record[field] = None
    record["reminder_texts"] = []
    return record


def validate(raw) -> list[str]:
    """Return a list of schema violations. Empty list means the object is valid."""
    if not isinstance(raw, dict):
        return ["not an object"]
    errors = []
    for field in FIELDS:
        if field not in raw:
            errors.append(f"missing {field}")
    if raw.get("operation") not in OPERATIONS:
        errors.append("bad operation")
    if raw.get("event_type") not in EVENT_TYPES:
        errors.append("bad event_type")
    for field in SPAN_FIELDS:
        value = raw.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"bad {field}")
    reminders = raw.get("reminder_texts")
    if not isinstance(reminders, list) or not all(isinstance(item, str) for item in reminders):
        errors.append("bad reminder_texts")
    return errors


def coerce(raw) -> dict:
    """Canonicalize a parsed object for scoring, tolerant of schema drift."""
    result = empty_extraction()
    if not isinstance(raw, dict):
        return result
    if raw.get("operation") in OPERATIONS:
        result["operation"] = raw["operation"]
    if raw.get("event_type") in EVENT_TYPES:
        result["event_type"] = raw["event_type"]
    for field in SPAN_FIELDS:
        value = raw.get(field)
        result[field] = value.strip() if isinstance(value, str) and value.strip() else None
    reminders = raw.get("reminder_texts")
    if isinstance(reminders, list):
        result["reminder_texts"] = [
            item.strip() for item in reminders if isinstance(item, str) and item.strip()
        ]
    return result
