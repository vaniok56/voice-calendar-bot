"""Hosted providers and the shared OpenAI-compatible client.

Groq, Gemini and Mistral all accept POST /chat/completions with a JSON
`response_format`, so they share one client. Keys come from .env and are never
written to results, and errors never echo the request body or the key.
"""

import json
import os
import time
import urllib.error
import urllib.request

import contract

_RETRYABLE = {429, 500, 502, 503, 504}

PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "env": "GROQ_API",
        "schema_mode": "json_schema",
        "token_param": "max_completion_tokens",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env": "GEMINI_API",
        "schema_mode": "json_schema",
        "token_param": "max_completion_tokens",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "env": "MISTRAL_API",
        "schema_mode": "json_schema",
        "token_param": "max_tokens",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "env": "DEEPSEEK_API",
        "schema_mode": "json_object",
        "token_param": "max_tokens",
        "request_options": {"thinking": {"type": "disabled"}},
    },
}


class ClientError(RuntimeError):
    """An API or transport error whose message is safe to store."""


def _response_format(mode: str) -> dict:
    if mode == "json_object":
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {"name": "event", "strict": True, "schema": contract.SCHEMA},
    }


def chat(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    *,
    schema_mode: str = "json_schema",
    token_param: str = "max_tokens",
    request_options: dict | None = None,
    timeout: int = 180,
    max_tokens: int = 2048,
) -> tuple[str, dict, float]:
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        token_param: max_tokens,
        "response_format": _response_format(schema_mode),
    }
    body.update(request_options or {})
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "llm-extraction-benchmark/1.0",
        },
        method="POST",
    )
    attempts = 0
    call_seconds = 0.0
    while True:
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read())
            call_seconds += time.perf_counter() - started
            break
        except urllib.error.HTTPError as error:
            call_seconds += time.perf_counter() - started
            retry_after = error.headers.get("Retry-After")
            if error.code in _RETRYABLE and attempts < 3:
                delay = float(retry_after) if (retry_after or "").isdigit() else min(2 ** attempts, 30)
                time.sleep(delay)
                attempts += 1
                continue
            raise ClientError(f"HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise ClientError(f"network error: {error.reason}") from error
        except json.JSONDecodeError as error:
            raise ClientError("invalid JSON response") from error

    choices = payload.get("choices") or []
    if not choices:
        raise ClientError("no choices in response")
    content = choices[0].get("message", {}).get("content", "")
    return content, payload.get("usage", {}), round(call_seconds, 3)


def parse_content(content: str) -> dict:
    """Parse model content into an object, stripping an optional code fence."""
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
        raise ClientError("model returned invalid JSON") from error
    if not isinstance(parsed, dict):
        raise ClientError("model returned invalid JSON")
    return parsed


def load_environment(path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name and name not in os.environ:
            os.environ[name] = value.strip().strip('"').strip("'")


def complete(provider: str, model: str, messages: list[dict],
             schema_mode: str | None = None) -> tuple[str, dict, float]:
    config = PROVIDERS[provider]
    api_key = os.environ.get(config["env"], "").strip()
    if not api_key:
        raise ClientError(f"missing {config['env']} in .env")
    return chat(
        config["base_url"],
        api_key,
        model,
        messages,
        schema_mode=schema_mode or config["schema_mode"],
        token_param=config["token_param"],
        request_options=config.get("request_options"),
    )
