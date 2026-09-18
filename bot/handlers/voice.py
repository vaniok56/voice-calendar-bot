import json
import logging
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from aiogram import F, Bot, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..asr import format_duration, ogg_duration_seconds, transcribe_voice
from ..drafts import Draft, apply_answer, next_field
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
        if resolved.get("date"):
            when = datetime.fromisoformat(resolved["date"]).strftime("%a %d %b %Y")
            lines.append(f"<b>When:</b> {when} (all day)")
        else:
            lines.append("<b>When:</b> all day")
    elif resolved.get("start"):
        start = datetime.fromisoformat(resolved["start"])
        timezone = start.tzname()
        lines.append(
            f"<b>Start:</b> {start.strftime('%a %d %b %Y, %H:%M')}"
            + (f" {timezone}" if timezone else "")
        )
    elif resolved.get("date"):
        when = datetime.fromisoformat(resolved["date"]).strftime("%a %d %b %Y")
        lines.append(f"<b>When:</b> {when}")
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


def _question_text(field: str, resolved: dict) -> str:
    if field == "title":
        return "❓ What should I call this event?"
    name = f" <b>{escape(resolved['title'])}</b>" if resolved.get("title") else ""
    if field == "date_text":
        return f"❓ Which date for{name}?"
    return f"❓ What time for{name}?"


def _question_keyboard(field: str, reference) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if field == "date_text":
        today, tomorrow = reference, reference + timedelta(days=1)
        rows.append([
            InlineKeyboardButton(
                text=f"Today · {today:%a %d %b}",
                callback_data=f"ans:date_text:{today.isoformat()}",
            ),
            InlineKeyboardButton(
                text=f"Tomorrow · {tomorrow:%a %d %b}",
                callback_data=f"ans:date_text:{tomorrow.isoformat()}",
            ),
        ])
    elif field == "time_text":
        rows.append([
            InlineKeyboardButton(text="09:00", callback_data="ans:time_text:09:00"),
            InlineKeyboardButton(text="12:00", callback_data="ans:time_text:12:00"),
            InlineKeyboardButton(text="18:00", callback_data="ans:time_text:18:00"),
        ])
        rows.append([
            InlineKeyboardButton(text="🌙 All day", callback_data="ans:time_text:all day"),
        ])
    rows.append([InlineKeyboardButton(text="Cancel", callback_data="q_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _deliver(message, bot, draft, text, markup):
    """Edit the draft's prompt message, or send a new one and remember its handle."""
    if draft is not None and draft.chat_id is not None and draft.message_id is not None:
        try:
            await bot.edit_message_text(
                chat_id=draft.chat_id,
                message_id=draft.message_id,
                text=text,
                reply_markup=markup,
            )
            return
        except TelegramBadRequest as error:
            if "not modified" in str(error).lower():
                return
    sent = await message.answer(text, reply_markup=markup)
    if draft is not None:
        draft.chat_id, draft.message_id = sent.chat.id, sent.message_id


async def reply_event(
    message: Message, bot, drafts, user_id, raw, resolved, error, *, show_raw=False
) -> None:
    if error is not None:
        await message.answer(f"⚠️ Extraction failed: <code>{escape(error)}</code>")
        return
    if show_raw:
        raw_json = json.dumps(raw, ensure_ascii=False, indent=2)
        await message.answer(f"🧠 <b>Extraction:</b>\n<pre>{escape(raw_json)}</pre>")

    draft = drafts.get(user_id)
    field = next_field(resolved)
    if field is None:
        text = format_resolved(resolved)
        if not resolved.get("complete"):
            text += "\n\n⚠️ Incomplete event"
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Confirm", callback_data=f"confirm:{message.message_id}"),
            InlineKeyboardButton(text="✏️ Edit", callback_data=f"edit:{message.message_id}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data=f"cancel:{message.message_id}"),
        ]])
        await _deliver(message, bot, draft, text, markup)
        drafts.pop(user_id, None)
        return

    if draft is None:
        draft = Draft(raw=dict(raw), awaiting=field)
        drafts[user_id] = draft
    text = f"{format_resolved(resolved)}\n\n{_question_text(field, resolved)}"
    await _deliver(message, bot, draft, text, _question_keyboard(field, datetime.now(CHISINAU).date()))


@router.callback_query(F.data == "q_cancel")
async def cancel_draft(callback: CallbackQuery, drafts) -> None:
    drafts.pop(callback.from_user.id, None)
    await callback.answer("Cancelled")
    await callback.message.edit_text("❌ Draft cancelled.")


@router.callback_query(F.data.startswith("ans:"))
async def answer_field(callback: CallbackQuery, bot: Bot, drafts) -> None:
    user_id = callback.from_user.id
    draft = drafts.get(user_id)
    _, field, value = callback.data.split(":", 2)
    if draft is None or field != draft.awaiting:
        await callback.answer("This question is no longer active.", show_alert=True)
        return
    await callback.answer()
    resolved = apply_answer(draft, value, resolve)
    await reply_event(callback.message, bot, drafts, user_id, draft.raw, resolved, None)



@router.message(F.voice)
async def handle_voice(
    message: Message,
    bot: Bot,
    drafts,
    elevenlabs_api_key: str,
    elevenlabs_model: str,
    mistral_api_key: str,
    extraction_model: str,
    extraction_timeout: int,
    voice_root: Path,
    voice_retention_hours: int,
    debug: bool,
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

        draft = drafts.get(user_id)
        if draft is not None:
            resolved = apply_answer(draft, result.text, resolve)
            record.update(
                status="completed",
                transcript=result.text,
                language_code=result.language_code,
                resolved=resolved,
            )
            _write_record(record_path, record)
            await reply_event(
                message, bot, drafts, user_id, draft.raw, resolved, None, show_raw=debug
            )
            return

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
        await reply_event(
            message, bot, drafts, user_id, extraction_raw, resolved, extraction_error, show_raw=debug
        )

        if debug:
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
    bot: Bot,
    drafts,
    mistral_api_key: str,
    extraction_model: str,
    extraction_timeout: int,
    text_root: Path,
    voice_retention_hours: int,
    debug: bool,
) -> None:
    user_id = message.from_user.id if message.from_user else 0

    draft = drafts.get(user_id)
    if draft is not None:
        resolved = apply_answer(draft, message.text, resolve)
        await reply_event(message, bot, drafts, user_id, draft.raw, resolved, None)
        return

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

        await reply_event(
            message, bot, drafts, user_id, extraction_raw, resolved, extraction_error, show_raw=debug
        )
        if debug:
            await message.answer(
                "🧠 <b>LLM wait:</b> "
                + (f"{extraction_seconds:.2f}s" if extraction_seconds is not None else "n/a")
            )
    except Exception as error:
        record.update(status="failed", error=f"{type(error).__name__}: {error}")
        _write_record(record_path, record)
        log.error("%s - text failed message_id=%s: %s", user_id, message.message_id, error)
        await message.answer(f"⚠️ Failed: {escape(str(error))}")
