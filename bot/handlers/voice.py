import json
import logging
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from aiogram import F, Bot, Router
from aiogram.enums import ChatAction
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..asr import format_duration, ogg_duration_seconds, transcribe_voice
from ..extraction import extract_event
from ..logging_config import CHISINAU
from ..resolver import resolve


router = Router(name="voice")
log = logging.getLogger(__name__)

MAX_VOICE_BYTES = 2 * 1024 * 1024  # 2 MiB
CREDITS_PER_AUDIO_SECOND = 10 / 9
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _human_minutes(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours} h {mins} min"
    if hours:
        return f"{hours} h"
    return f"{mins} min"


def format_resolved(resolved: dict) -> str:
    lines = ["📅 <b>Resolved</b>"]
    if resolved.get("title"):
        lines.append(f"<b>Title:</b> {escape(resolved['title'])}")
    lines.append(f"<b>Type:</b> {escape(str(resolved.get('event_type') or 'other'))}")
    if resolved.get("all_day"):
        lines.append("<b>When:</b> all day")
    elif resolved.get("start"):
        start = datetime.fromisoformat(resolved["start"])
        timezone = start.tzname()
        lines.append(
            f"<b>Start:</b> {start.strftime('%a %d %b %Y, %H:%M')}"
            + (f" {timezone}" if timezone else "")
        )
    duration = _human_minutes(resolved.get("duration_minutes"))
    if duration:
        lines.append(f"<b>Duration:</b> {duration}")
    if resolved.get("location"):
        lines.append(f"<b>Location:</b> {escape(resolved['location'])}")
    recurrence = resolved.get("recurrence")
    if recurrence:
        weekday = ""
        if recurrence.get("weekday") is not None:
            weekday = f" on {WEEKDAY_NAMES[recurrence['weekday']]}"
        lines.append(f"<b>Repeats:</b> {escape(str(recurrence.get('freq', '')))}{weekday}")
    reminders = resolved.get("reminders_minutes") or []
    if reminders:
        human = ", ".join(_human_minutes(value) for value in sorted(reminders, reverse=True))
        lines.append(f"<b>Reminders:</b> {human} before")
    if resolved.get("ambiguous"):
        lines.append("⚠️ Time is ambiguous — please confirm")
    if not resolved.get("complete"):
        lines.append("⚠️ Incomplete event")
    return "\n".join(lines)



def _write_record(path: Path, record: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)


async def run_extraction(
    text: str, *, mistral_api_key: str, extraction_model: str, extraction_timeout: int
):
    try:
        extraction = await extract_event(
            text, api_key=mistral_api_key, model=extraction_model, timeout=extraction_timeout
        )
        return extraction.raw, resolve(extraction.raw), extraction.wait_time_seconds, None
    except Exception as error:
        return None, None, None, f"{type(error).__name__}: {error}"


async def reply_event(message: Message, raw, resolved, seconds, error) -> None:
    if error is not None:
        await message.answer(f"⚠️ Extraction failed: <code>{escape(error)}</code>")
        return
    raw_json = json.dumps(raw, ensure_ascii=False, indent=2)
    await message.answer(f"🧠 <b>Extraction:</b>\n<pre>{escape(raw_json)}</pre>")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Confirm", callback_data=f"confirm:{message.message_id}"),
        InlineKeyboardButton(text="🔁 Retry", callback_data=f"retry:{message.message_id}"),
        InlineKeyboardButton(text="❌ Cancel", callback_data=f"cancel:{message.message_id}"),
    ]])
    await message.answer(format_resolved(resolved), reply_markup=keyboard)



@router.message(F.voice)
async def handle_voice(
    message: Message,
    bot: Bot,
    elevenlabs_api_key: str,
    elevenlabs_model: str,
    mistral_api_key: str,
    extraction_model: str,
    extraction_timeout: int,
    voice_root: Path,
    voice_retention_hours: int,
) -> None:
    voice = message.voice
    if voice is None or voice.file_size is None or voice.file_size <= 0:
        await message.answer("Voice message has no usable audio data.")
        return

    if voice.file_size > MAX_VOICE_BYTES:
        await message.answer("Voice message exceeds the 2 MiB limit.")
        return

    user_id = message.from_user.id if message.from_user else 0
    received_at = datetime.now(CHISINAU)
    record_dir = voice_root / str(user_id) / str(message.message_id)
    try:
        record_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        return

    record_path = record_dir / "record.json"
    audio_path = record_dir / "audio.ogg"
    record = {
        "received_at": received_at.isoformat(),
        "expires_at": (received_at + timedelta(hours=voice_retention_hours)).isoformat(),
        "status": "processing",
        "transcript": None,
        "language_code": None,
        "extraction": None,
        "resolved": None,
        "extraction_error": None,
        "error": None,
    }
    _write_record(record_path, record)

    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        await bot.download(voice, destination=audio_path)

        result = await transcribe_voice(
            audio_path, api_key=elevenlabs_api_key, model_id=elevenlabs_model
        )

        extraction_raw, resolved, extraction_seconds, extraction_error = await run_extraction(
            result.text,
            mistral_api_key=mistral_api_key,
            extraction_model=extraction_model,
            extraction_timeout=extraction_timeout,
        )
        if extraction_error is not None:
            log.error(
                "%s - extraction failed message_id=%s: %s",
                user_id,
                message.message_id,
                extraction_error,
            )

        record.update(
            status="completed",
            transcript=result.text,
            language_code=result.language_code,
            extraction=extraction_raw,
            resolved=resolved,
            extraction_error=extraction_error,
        )
        _write_record(record_path, record)

        log.info(
            "%s - voice transcribed message_id=%s lang=%s",
            user_id,
            message.message_id,
            result.language_code,
        )

        actual_seconds = ogg_duration_seconds(audio_path) or float(voice.duration or 0)
        estimated_credits = round(actual_seconds * CREDITS_PER_AUDIO_SECOND)

        await message.answer(escape(result.text))
        await reply_event(message, extraction_raw, resolved, extraction_seconds, extraction_error)

        summary = [
            f"⏱️ <b>ASR wait:</b> {result.wait_time_seconds:.2f}s",
            f"🧠 <b>LLM wait:</b> "
            + (f"{extraction_seconds:.2f}s" if extraction_seconds is not None else "n/a"),
            f"🌐 <b>Language:</b> {escape(result.language_code or 'unknown')}",
            f"🎙️ <b>Audio length:</b> {escape(format_duration(voice.duration))}",
            f"💳 <b>ASR cost:</b> ~{estimated_credits} credits",
        ]
        await message.answer("\n".join(summary))
    except Exception as error:
        record.update(status="failed", error=f"{type(error).__name__}: {error}")
        _write_record(record_path, record)
        log.error(
            "%s - voice failed message_id=%s: %s",
            user_id,
            message.message_id,
            error,
        )
        await message.answer(f"⚠️ Transcription failed: {escape(str(error))}")


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text(
    message: Message,
    mistral_api_key: str,
    extraction_model: str,
    extraction_timeout: int,
    text_root: Path,
    voice_retention_hours: int,
) -> None:
    user_id = message.from_user.id if message.from_user else 0
    received_at = datetime.now(CHISINAU)
    record_dir = text_root / str(user_id) / str(message.message_id)
    try:
        record_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        return

    record_path = record_dir / "record.json"
    record = {
        "received_at": received_at.isoformat(),
        "expires_at": (received_at + timedelta(hours=voice_retention_hours)).isoformat(),
        "status": "processing",
        "text": message.text,
        "extraction": None,
        "resolved": None,
        "extraction_error": None,
        "error": None,
    }
    _write_record(record_path, record)

    try:
        extraction_raw, resolved, extraction_seconds, extraction_error = await run_extraction(
            message.text,
            mistral_api_key=mistral_api_key,
            extraction_model=extraction_model,
            extraction_timeout=extraction_timeout,
        )
        record.update(
            status="completed",
            extraction=extraction_raw,
            resolved=resolved,
            extraction_error=extraction_error,
        )
        _write_record(record_path, record)
        if extraction_error is not None:
            log.error(
                "%s - text extraction failed message_id=%s: %s",
                user_id,
                message.message_id,
                extraction_error,
            )

        await reply_event(message, extraction_raw, resolved, extraction_seconds, extraction_error)
        await message.answer(
            "🧠 <b>LLM wait:</b> "
            + (f"{extraction_seconds:.2f}s" if extraction_seconds is not None else "n/a")
        )
    except Exception as error:
        record.update(status="failed", error=f"{type(error).__name__}: {error}")
        _write_record(record_path, record)
        log.error("%s - text failed message_id=%s: %s", user_id, message.message_id, error)
        await message.answer(f"⚠️ Failed: {escape(str(error))}")
