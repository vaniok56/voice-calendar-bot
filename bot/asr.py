import asyncio
import json
import mimetypes
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ASRServiceError(Exception):
    """Raised when ASR transcription fails."""


@dataclass(frozen=True)
class TranscriptionResponse:
    text: str
    language_code: str | None
    wait_time_seconds: float


def format_duration(seconds: int | float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes = total_seconds // 60
    remaining_secs = total_seconds % 60
    return f"{minutes}:{remaining_secs:02d} ({total_seconds}s)"


OPUS_SAMPLE_RATE = 48000


def ogg_duration_seconds(file_path: Path) -> float | None:
    """Exact audio duration from the last Ogg page's granule position."""
    try:
        data = file_path.read_bytes()
    except OSError:
        return None
    index = data.rfind(b"OggS")
    if index < 0:
        return None
    granule = int.from_bytes(data[index + 6 : index + 14], "little")
    return granule / OPUS_SAMPLE_RATE


def _multipart_payload(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    boundary = f"----voice-bot-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend((
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode(),
            b"\r\n",
        ))
    mime_type = mimetypes.guess_type(file_path.name)[0] or "audio/ogg"
    chunks.extend((
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode(),
        f"Content-Type: {mime_type}\r\n\r\n".encode(),
        file_path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _sync_transcribe(file_path: Path, api_key: str, model_id: str, timeout: int) -> TranscriptionResponse:
    body, content_type = _multipart_payload({"model_id": model_id}, file_path)
    url = "https://api.elevenlabs.io/v1/speech-to-text"
    request = Request(
        url,
        data=body,
        headers={
            "xi-api-key": api_key,
            "Content-Type": content_type,
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            wait_time = time.perf_counter() - started
            data = json.loads(raw.decode("utf-8")) if raw else {}
    except HTTPError as error:
        try:
            body_json = json.loads(error.read().decode("utf-8"))
            detail = body_json.get("detail", {})
            if isinstance(detail, dict):
                msg = detail.get("message", "")
            else:
                msg = str(detail)
        except Exception:
            msg = ""
        msg = (" " + msg.strip()) if msg else ""
        raise ASRServiceError(f"ElevenLabs API error (HTTP {error.code}{msg})") from error
    except URLError as error:
        raise ASRServiceError(f"Network error contacting ElevenLabs: {error.reason}") from error
    except TimeoutError as error:
        raise ASRServiceError("ElevenLabs request timed out") from error
    except json.JSONDecodeError as error:
        raise ASRServiceError("ElevenLabs returned invalid JSON") from error

    text = data.get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise ASRServiceError("ElevenLabs returned an empty transcript")

    language_code = data.get("language_code")
    return TranscriptionResponse(
        text=text.strip(),
        language_code=str(language_code) if language_code else None,
        wait_time_seconds=round(wait_time, 2),
    )


async def transcribe_voice(
    file_path: Path,
    api_key: str,
    model_id: str = "scribe_v2",
    timeout: int = 90,
) -> TranscriptionResponse:
    """Non-blocking call to ElevenLabs Scribe v2."""
    return await asyncio.to_thread(_sync_transcribe, file_path, api_key, model_id, timeout)
