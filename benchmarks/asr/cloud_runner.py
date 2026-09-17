#!/usr/bin/env python3
"""Run the frozen private ASR corpus against hosted transcription APIs.

Results are appended one recording at a time, so an interrupted or rate-limited
run can safely be resumed.  Credentials are read from .env but are never
written to result files or logs.
"""

import argparse
import asyncio
import base64
import json
import mimetypes
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import run as benchmark


ROOT = Path(__file__).resolve().parents[2]
VOICE = ROOT / "data" / "voice"
RESULTS = ROOT / "data" / "asr-benchmark" / "results"
POLL_SECONDS = 2
POLL_TIMEOUT_SECONDS = 10 * 60
GEMINI_DAILY_REQUEST_LIMIT = 25
GEMINI_REQUEST_INTERVAL_SECONDS = 21
LIVE_FINAL_IDLE_SECONDS = 3
LIVE_FINAL_TIMEOUT_SECONDS = 30
MODELS = ("gemini", "gemini-live", "assemblyai", "elevenlabs")


class CloudError(RuntimeError):
    """An API error whose message is safe to save in the private result file."""


@dataclass
class TranscriptionResult:
    transcript: str
    detected_language: str | None = None
    metrics: dict[str, float] = field(default_factory=dict)


def load_environment(path: Path) -> None:
    """Load simple .env entries without adding a benchmark-only dependency."""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name and name not in os.environ:
            os.environ[name] = value.strip('"').strip("'")


def request_json(url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
                 payload: bytes | None = None, timeout: int = 90) -> tuple[dict[str, Any], Any]:
    request = Request(url, data=payload, headers=headers or {}, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return (json.loads(raw) if raw else {}), response.headers
    except HTTPError as error:
        # Preserve only the standard API error message, never the request or
        # headers (which could contain credentials or uploaded-audio details).
        try:
            body = json.loads(error.read())
            message = str(body.get("error", {}).get("message", ""))
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            message = ""
        message = " ".join(message.split())[:300]
        suffix = f": {message}" if message else ""
        raise CloudError(f"HTTP {error.code}{suffix}") from error
    except URLError as error:
        raise CloudError(f"network error: {error.reason}") from error


def multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    boundary = f"----voice-benchmark-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend((
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode(), b"\r\n",
        ))
    mime_type = mimetypes.guess_type(file_path.name)[0] or "audio/ogg"
    chunks.extend((
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode(),
        f"Content-Type: {mime_type}\r\n\r\n".encode(),
        file_path.read_bytes(), b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def gemini_transcribe(audio_path: Path, api_key: str) -> TranscriptionResult:
    mime_type = "audio/ogg"
    start_headers = {
        "x-goog-api-key": api_key,
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(audio_path.stat().st_size),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "Content-Type": "application/json",
    }
    _, upload_headers = request_json(
        "https://generativelanguage.googleapis.com/upload/v1beta/files",
        method="POST", headers=start_headers,
        payload=json.dumps({"file": {"display_name": audio_path.name}}).encode(),
    )
    upload_url = upload_headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise CloudError("Gemini did not return an upload URL")
    file_info, _ = request_json(upload_url, method="POST", headers={
        "Content-Length": str(audio_path.stat().st_size),
        "X-Goog-Upload-Offset": "0",
        "X-Goog-Upload-Command": "upload, finalize",
    }, payload=audio_path.read_bytes())
    file_name = file_info.get("file", {}).get("name")
    file_uri = file_info.get("file", {}).get("uri")
    if not file_name or not file_uri:
        raise CloudError("Gemini did not return uploaded file metadata")
    try:
        response, _ = request_json(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            method="POST", headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            payload=json.dumps({
                "model": "gemini-3.5-transcribe",
                "input": [{"type": "audio", "uri": file_uri, "mime_type": mime_type}],
                "generation_config": {"transcription_config": {"language_codes": []}},
            }).encode(),
        )
    finally:
        try:
            request_json(
                f"https://generativelanguage.googleapis.com/v1beta/{file_name}",
                method="DELETE", headers={"x-goog-api-key": api_key},
            )
        except CloudError:
            pass
    transcript = response.get("output_text", "")
    if not transcript:
        # The REST Interactions response exposes text in `outputs`; the SDK
        # convenience property is named `output_text`.
        transcript = next(
            (output.get("text", "") for output in response.get("outputs", [])
             if output.get("type") == "text" and output.get("text")),
            "",
        )
    if not transcript:
        transcript = next(
            (content.get("text", "") for step in response.get("steps", [])
             for content in step.get("content", [])
             if content.get("type") == "text" and content.get("text")),
            "",
        )
    if not isinstance(transcript, str) or not transcript.strip():
        raise CloudError("Gemini returned no transcript")
    return TranscriptionResult(transcript)


def decode_pcm(audio_path: Path) -> bytes:
    """Decode Telegram OGG/Opus to the Live API's PCM input format."""
    process = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(audio_path), "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
        check=False, capture_output=True,
    )
    if process.returncode or not process.stdout:
        raise CloudError("ffmpeg could not decode the recording to PCM")
    return process.stdout


def parse_live_response(response: Any) -> tuple[str | None, bool]:
    """Extract one finalized segment and completion state from a Live response."""
    if not isinstance(response, dict):
        raise CloudError("Gemini Live returned an invalid response")
    if response.get("error"):
        error = response["error"] if isinstance(response["error"], dict) else {}
        message = " ".join(str(error.get("message", "")).split())[:300]
        suffix = f": {message}" if message else ""
        raise CloudError(f"Gemini Live API error{suffix}")
    content = response.get("serverContent", {})
    if not isinstance(content, dict):
        return None, False
    transcription = content.get("inputTranscription", {})
    text = transcription.get("text") if isinstance(transcription, dict) else None
    final_text = text.strip() if isinstance(text, str) and text.strip() else None
    return final_text, bool(content.get("turnComplete"))


async def wait_for_live_final(
    transcripts: list[str],
    final_received: asyncio.Event,
    turn_complete: asyncio.Event,
    receiver: asyncio.Task[None],
) -> None:
    """Wait until finalized transcript events settle after audioStreamEnd."""
    deadline = asyncio.get_running_loop().time() + LIVE_FINAL_TIMEOUT_SECONDS
    while True:
        if receiver.done():
            await receiver
        if turn_complete.is_set():
            return
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            if transcripts:
                return
            raise CloudError("Gemini Live returned no final transcript before timeout")
        final_received.clear()
        try:
            await asyncio.wait_for(
                final_received.wait(),
                timeout=min(LIVE_FINAL_IDLE_SECONDS, remaining),
            )
        except TimeoutError:
            if transcripts:
                return


def gemini_live_transcribe(audio_path: Path, api_key: str) -> TranscriptionResult:
    """Send one finished recording through the Gemini Live WebSocket API."""
    try:
        from websockets.asyncio.client import connect
    except ImportError as error:
        raise CloudError("Gemini Live needs the optional websockets package") from error

    url = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?"
        + urlencode({"key": api_key})
    )
    setup = {
        "setup": {
            "model": "models/gemini-3.5-transcribe-live",
            "generationConfig": {"responseModalities": ["TEXT"]},
            "inputAudioTranscription": {"languageCodes": []},
        }
    }

    async def stream() -> tuple[str, dict[str, float]]:
        transcripts: list[str] = []
        final_received = asyncio.Event()
        turn_complete = asyncio.Event()
        last_final_at: float | None = None
        pcm = decode_pcm(audio_path)
        audio_seconds = len(pcm) / 32_000
        stream_started = time.perf_counter()
        async with connect(url, open_timeout=30, close_timeout=5) as socket:
            await socket.send(json.dumps(setup))
            setup_response = json.loads(await asyncio.wait_for(socket.recv(), timeout=30))
            if not isinstance(setup_response, dict) or "setupComplete" not in setup_response:
                parse_live_response(setup_response)
                raise CloudError("Gemini Live did not confirm setup")

            async def receive_transcripts() -> None:
                nonlocal last_final_at
                while not turn_complete.is_set():
                    final_text, complete = parse_live_response(json.loads(await socket.recv()))
                    if final_text:
                        transcripts.append(final_text)
                        last_final_at = time.perf_counter()
                        final_received.set()
                    if complete:
                        turn_complete.set()

            receiver = asyncio.create_task(receive_transcripts())
            try:
                # 100 ms of 16 kHz, 16-bit mono audio is 3,200 bytes.
                for index in range(0, len(pcm), 3200):
                    chunk = pcm[index:index + 3200]
                    await socket.send(json.dumps({"realtimeInput": {"audio": {
                        "data": base64.b64encode(chunk).decode("ascii"),
                        "mimeType": "audio/pcm;rate=16000",
                    }}}))
                    if index + 3200 < len(pcm):
                        await asyncio.sleep(len(chunk) / 32_000)
                await socket.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                audio_finished = time.perf_counter()
                await wait_for_live_final(
                    transcripts, final_received, turn_complete, receiver,
                )
                finished = time.perf_counter()
            finally:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)
        elapsed = finished - stream_started
        return " ".join(transcripts).strip(), {
            "audio_seconds": audio_seconds,
            "finalization_seconds": max(0, (last_final_at or finished) - audio_finished),
            "real_time_factor": elapsed / audio_seconds if audio_seconds else 0,
        }

    try:
        transcript, metrics = asyncio.run(stream())
    except CloudError:
        raise
    except Exception as error:
        # Do not retain the WebSocket exception: it may include the connection
        # URL, and its query string contains the API key.
        raise CloudError(f"Gemini Live request failed ({type(error).__name__})") from error
    if not transcript:
        raise CloudError("Gemini Live returned no final transcript")
    return TranscriptionResult(transcript, metrics=metrics)


def assemblyai_transcribe(audio_path: Path, api_key: str) -> TranscriptionResult:
    upload_headers = {"authorization": api_key, "Content-Type": "application/octet-stream"}
    upload, _ = request_json("https://api.assemblyai.com/v2/upload", method="POST",
                             headers=upload_headers, payload=audio_path.read_bytes())
    audio_url = upload.get("upload_url")
    if not audio_url:
        raise CloudError("AssemblyAI did not return an upload URL")
    created, _ = request_json("https://api.assemblyai.com/v2/transcript", method="POST", headers={
        "authorization": api_key, "Content-Type": "application/json",
    }, payload=json.dumps({"audio_url": audio_url, "speech_models": ["universal-2"],
                           "language_detection": True}).encode())
    transcript_id = created.get("id")
    if not transcript_id:
        raise CloudError("AssemblyAI did not return a transcript ID")
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        result, _ = request_json(f"https://api.assemblyai.com/v2/transcript/{transcript_id}", headers={
            "authorization": api_key,
        })
        if result.get("status") == "completed":
            return TranscriptionResult(str(result.get("text", "")), result.get("language_code"))
        if result.get("status") == "error":
            raise CloudError("AssemblyAI transcription failed")
        time.sleep(POLL_SECONDS)
    raise CloudError("AssemblyAI transcription timed out")


def elevenlabs_transcribe(audio_path: Path, api_key: str) -> TranscriptionResult:
    body, content_type = multipart({"model_id": "scribe_v2"}, audio_path)
    result, _ = request_json("https://api.elevenlabs.io/v1/speech-to-text", method="POST", headers={
        "xi-api-key": api_key, "Content-Type": content_type,
    }, payload=body)
    transcript = result.get("text", "")
    if not isinstance(transcript, str) or not transcript.strip():
        raise CloudError("ElevenLabs returned no transcript")
    return TranscriptionResult(transcript, result.get("language_code"))


def append_result(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False) + "\n")
    path.chmod(0o600)


def successful_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    successful = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            if record.get("status") == "ok":
                successful.add(record["id"])
        except (json.JSONDecodeError, KeyError):
            pass
    return successful


def run_model(provider: str, api_key: str, max_requests: int | None, retry_errors: bool) -> None:
    names = {"gemini": "gemini-3.5-transcribe", "gemini-live": "gemini-3.5-transcribe-live", "assemblyai": "assemblyai-universal-2",
             "elevenlabs": "elevenlabs-scribe-v2"}
    functions = {"gemini": gemini_transcribe, "gemini-live": gemini_live_transcribe, "assemblyai": assemblyai_transcribe,
                 "elevenlabs": elevenlabs_transcribe}
    output = RESULTS / f"{names[provider]}.jsonl"
    completed = successful_ids(output) if retry_errors else benchmark.completed_ids(output)
    attempted = 0
    previous_start: float | None = None
    for item in json.loads(benchmark.MANIFEST.read_text(encoding="utf-8")):
        if item["id"] in completed:
            continue
        if max_requests is not None and attempted >= max_requests:
            print(f"{provider}: request limit reached; rerun later to resume", flush=True)
            break
        if provider == "gemini" and previous_start is not None:
            remaining = GEMINI_REQUEST_INTERVAL_SECONDS - (time.monotonic() - previous_start)
            if remaining > 0:
                time.sleep(remaining)
        audio_path = VOICE / item["source_file"]
        started = time.perf_counter()
        previous_start = time.monotonic()
        record: dict[str, Any] = {"id": item["id"], "model": names[provider]}
        attempted += 1
        try:
            result = functions[provider](audio_path, api_key)
            record.update({"status": "ok", "transcript": result.transcript.strip(),
                           "detected_language": result.detected_language, **result.metrics})
        except Exception as error:
            record.update({"status": "error", "error": f"{type(error).__name__}: {error}"})
        record["elapsed_seconds"] = time.perf_counter() - started
        append_result(output, record)
        print(f"{provider}: {item['id']} {record['status']}", flush=True)


def main() -> None:
    global VOICE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("providers", nargs="*", choices=MODELS)
    parser.add_argument("--gemini-limit", type=int, default=GEMINI_DAILY_REQUEST_LIMIT,
                        help="maximum Gemini requests in this run (default: 25)")
    parser.add_argument("--max-requests", type=int,
                        help="maximum requests for each selected provider")
    parser.add_argument("--retry-errors", action="store_true",
                        help="retry recordings that have prior error results")
    parser.add_argument("--voice-dir", type=Path, default=VOICE,
                        help="directory containing paired OGG and JSON corpus files")
    args = parser.parse_args()
    providers = args.providers or MODELS
    load_environment(ROOT / ".env")
    keys = {"gemini": os.getenv("GEMINI_API", "").strip(),
            "gemini-live": os.getenv("GEMINI_API", "").strip(),
            "assemblyai": os.getenv("ASSEMBLYAI_API", "").strip(),
            "elevenlabs": os.getenv("ELLEVENLABS_API", "").strip()}
    missing = [provider for provider in providers if not keys[provider]]
    if missing:
        raise SystemExit("Missing API key configuration for: " + ", ".join(missing))
    VOICE = args.voice_dir.resolve()
    benchmark.VOICE = VOICE
    benchmark.create_manifest()
    for provider in providers:
        limit = args.max_requests
        if limit is None and provider == "gemini":
            limit = args.gemini_limit
        run_model(provider, keys[provider], limit, args.retry_errors)
    benchmark.score_all()


if __name__ == "__main__":
    main()
