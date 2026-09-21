"""DeepSeek semantic extraction over its OpenAI-compatible HTTP API."""

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
_RETRYABLE = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


class ExtractionServiceError(Exception):
    """Raised when extraction fails; message is safe to store and show."""


@dataclass(frozen=True)
class Extraction:
    raw: dict
    wait_time_seconds: float


def _request_body(model: str, messages: list[dict]) -> dict:
    return {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
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


def _sync_extract(messages: list[dict], api_key: str, model: str, timeout: int) -> Extraction:
    request = urllib.request.Request(
        DEEPSEEK_BASE_URL + "/chat/completions",
        data=json.dumps(_request_body(model, messages)).encode("utf-8"),
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
            raise ExtractionServiceError(f"DeepSeek API error (HTTP {error.code})") from error
        except urllib.error.URLError as error:
            raise ExtractionServiceError(f"Network error contacting DeepSeek: {error.reason}") from error
        except TimeoutError as error:
            raise ExtractionServiceError("DeepSeek request timed out") from error
        except json.JSONDecodeError as error:
            last_error = ExtractionServiceError("DeepSeek returned invalid JSON")
            if retry:
                continue
            raise last_error from error

        choices = payload.get("choices") or []
        if not choices:
            last_error = ExtractionServiceError("DeepSeek returned no choices")
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
    raise last_error or ExtractionServiceError("DeepSeek extraction failed")


async def extract_event(
    messages: list[dict],
    *,
    api_key: str,
    model: str = "deepseek-flash",
    timeout: int = 60,
) -> Extraction:
    """Return model JSON without blocking bot event loop."""
    return await asyncio.to_thread(_sync_extract, messages, api_key, model, timeout)
