"""Cloud LLM extraction: transcript -> flat JSON event fields.

Mistral chat-completions with a strict JSON schema. The prompt and schema are
copied from the benchmark contract so the bot depends on nothing under
benchmarks/. The key is read from the environment and never logged.
"""

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

OPERATIONS = ("create", "edit", "delete", "list")
EVENT_TYPES = (
    "meeting", "appointment", "class", "exam", "call",
    "trip", "birthday", "reminder", "task", "other",
)
SPAN_FIELDS = (
    "title", "date_text", "time_text", "duration_text",
    "end_time_text", "location_text", "recurrence_text",
)
FIELDS = ("operation", "event_type", *SPAN_FIELDS, "reminder_texts")

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(FIELDS),
    "properties": {
        "operation": {"type": "string", "enum": list(OPERATIONS)},
        "event_type": {"type": "string", "enum": list(EVENT_TYPES)},
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

MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
_RETRYABLE = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


class ExtractionServiceError(Exception):
    """Raised when extraction fails; message is safe to store and show."""


@dataclass(frozen=True)
class Extraction:
    raw: dict
    wait_time_seconds: float


def _request_body(model: str, text: str) -> dict:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
        "max_tokens": 512,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "event", "strict": True, "schema": SCHEMA},
        },
    }


def _parse_content(content: str) -> dict:
    text = content.strip()
    if "```" in text:
        blocks = [block for block in text.split("```") if "{" in block]
        text = max(blocks, key=len) if blocks else text
    text = text.strip()
    if text.startswith("json"):
        text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ExtractionServiceError("model returned invalid JSON") from error
    if not isinstance(parsed, dict):
        raise ExtractionServiceError("model returned invalid JSON")
    return parsed


def _sync_extract(text: str, api_key: str, model: str, timeout: int) -> Extraction:
    request = urllib.request.Request(
        MISTRAL_BASE_URL + "/chat/completions",
        data=json.dumps(_request_body(model, text)).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "voice-calendar-bot/1.0",
        },
        method="POST",
    )
    started = time.perf_counter()
    last_error: ExtractionServiceError | None = None
    for attempt in range(_MAX_ATTEMPTS):
        retry = attempt < _MAX_ATTEMPTS - 1
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code in _RETRYABLE and retry:
                retry_after = error.headers.get("Retry-After")
                time.sleep(float(retry_after) if (retry_after or "").isdigit() else min(2 ** attempt, 30))
                continue
            raise ExtractionServiceError(f"Mistral API error (HTTP {error.code})") from error
        except urllib.error.URLError as error:
            raise ExtractionServiceError(f"Network error contacting Mistral: {error.reason}") from error
        except TimeoutError as error:
            raise ExtractionServiceError("Mistral request timed out") from error
        except json.JSONDecodeError as error:
            last_error = ExtractionServiceError("Mistral returned invalid JSON")
            if retry:
                continue
            raise last_error from error

        choices = payload.get("choices") or []
        if not choices:
            last_error = ExtractionServiceError("Mistral returned no choices")
            if retry:
                continue
            raise last_error
        content = choices[0].get("message", {}).get("content", "")
        try:
            raw = _parse_content(content)
        except ExtractionServiceError as error:
            last_error = error
            if retry:
                continue
            raise
        wait_time = round(time.perf_counter() - started, 2)
        return Extraction(raw=raw, wait_time_seconds=wait_time)
    raise last_error or ExtractionServiceError("Mistral extraction failed")


async def extract_event(
    text: str,
    api_key: str,
    model: str = "mistral-large-latest",
    timeout: int = 60,
) -> Extraction:
    """Non-blocking Mistral call: transcript text -> flat event JSON."""
    return await asyncio.to_thread(_sync_extract, text, api_key, model, timeout)
