import json
import logging
from copy import deepcopy
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

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
from ..calendar import (
    CalendarAPIError,
    CalendarAuthError,
    CalendarPayloadError,
    _token_is_expired,
    build_event,
    claim_write,
    connection_generation,
    create_write,
    edit_payload,
    insert_event,
    load_token,
    load_write,
    new_google_event_id,
    refresh_access_token,
    release_write,
    replace_write_payload,
    save_token,
    token_is_usable,
    update_write,
    oauth_is_configured,
)
from ..drafts import Draft, apply_answer, next_field
from ..extraction import extract_event
from ..logging_config import CHISINAU
from .. import semantic


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
    if resolved.get("auto_write"):
        lines.append("✅ Ready for automatic creation")
    else:
        lines.append("⚠️ Needs confirmation")
    if resolved.get("errors"):
        lines.append("<b>Missing or invalid:</b> " + escape(", ".join(resolved["errors"])))
    if resolved.get("risks"):
        lines.append("<b>Check:</b> " + escape(", ".join(resolved["risks"])))
    return "\n".join(lines)



def _write_record(path: Path, record: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)


def _update_record(path: Path, **updates) -> None:
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(updates)
    _write_record(path, record)


def _source_record(record_path: Path, config) -> str:
    try:
        return record_path.relative_to(config.data_dir).as_posix()
    except ValueError as error:
        raise CalendarPayloadError("Calendar source record is outside data storage") from error


def _calendar_markup(
    write_id: str, all_day: bool, *, retry: bool = False
) -> InlineKeyboardMarkup:
    del all_day
    if retry:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Retry", callback_data=f"confirm:{write_id}"),
        ]])
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Confirm", callback_data=f"confirm:{write_id}"),
            InlineKeyboardButton(text="✏️ Edit", callback_data=f"edit:{write_id}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data=f"cancel:{write_id}"),
        ],
    ])


def _edit_fields_markup(write_id: str, all_day: bool) -> InlineKeyboardMarkup:
    fields = [
        InlineKeyboardButton(text="Title", callback_data=f"edit_field:{write_id}:title"),
        InlineKeyboardButton(text="Date", callback_data=f"edit_field:{write_id}:date"),
    ]
    if not all_day:
        fields.append(InlineKeyboardButton(text="Time", callback_data=f"edit_field:{write_id}:time"))
    return InlineKeyboardMarkup(inline_keyboard=[
        fields,
        [InlineKeyboardButton(text="Back", callback_data=f"edit_back:{write_id}")],
    ])


def _calendar_text(payload: dict, status: str, html_link: str | None = None) -> str:
    lines = ["📅 <b>Calendar event</b>", f"<b>Title:</b> {escape(payload['summary'])}"]
    start = payload["start"]
    if "date" in start:
        lines.append(f"<b>When:</b> {escape(start['date'])} (all day)")
    else:
        lines.append(f"<b>Start:</b> {escape(start['dateTime'])}")
    if payload.get("location"):
        lines.append(f"<b>Location:</b> {escape(payload['location'])}")
    lines.append(status)
    if html_link:
        lines.append(f'<a href="{escape(html_link, quote=True)}">Open in Google Calendar</a>')
    return "\n".join(lines)


async def _execute_calendar_write(config, write: dict) -> dict:
    write_id = write["write_id"]
    try:
        try:
            token = load_token(
                config.data_dir, write["telegram_user_id"], config.calendar_token_encryption_key
            )
            if not token_is_usable(token):
                raise CalendarAuthError("Reconnect Google Calendar in /settings")
            generation = write.get("connection_generation", 0)
            if (
                connection_generation(config.data_dir, write["telegram_user_id"]) != generation
                or token.get("connection_generation") != generation
            ):
                raise CalendarAuthError("Google Calendar connection changed")
            if _token_is_expired(token):
                refreshed = await refresh_access_token(config, token)
                current = load_token(
                    config.data_dir, write["telegram_user_id"], config.calendar_token_encryption_key
                )
                if (
                    current is None
                    or not token_is_usable(current)
                    or current.get("refresh_token") != token.get("refresh_token")
                    or connection_generation(config.data_dir, write["telegram_user_id"])
                    != write.get("connection_generation", 0)
                ):
                    raise CalendarAuthError("Google Calendar connection was removed")
                token = {**token, **refreshed}
                save_token(
                    config.data_dir, write["telegram_user_id"], token,
                    config.calendar_token_encryption_key,
                )
            event = await insert_event(token, write["payload"])
        except (CalendarAuthError, CalendarAPIError, CalendarPayloadError) as error:
            return update_write(config.data_dir, write_id, status="failed", error=str(error))
        return update_write(
            config.data_dir,
            write_id,
            status="created",
            google_event_id=event.get("id"),
            google_html_link=event.get("htmlLink"),
            error=None,
        )
    finally:
        release_write(write_id)


async def run_extraction(
    text: str,
    reference: datetime,
    *,
    deepseek_api_key: str,
    extraction_model: str,
    extraction_timeout: int,
    zone: ZoneInfo = semantic.ZONE,
):
    try:
        messages = semantic.build_messages(text, reference, zone)
        extraction = await extract_event(
            messages,
            api_key=deepseek_api_key,
            model=extraction_model,
            timeout=extraction_timeout,
        )
        resolved = semantic.resolve(extraction.raw, text, reference, zone)
        return extraction.raw, resolved, extraction.wait_time_seconds, None
    except Exception as error:
        return None, None, None, f"{type(error).__name__}: {error}"


def _question_text(field: str, resolved: dict) -> str:
    if field == "title":
        return "❓ What should I call this event? Reply with a title."
    name = f" <b>{escape(resolved['title'])}</b>" if resolved.get("title") else ""
    if field == "date":
        return f"❓ Which date for{name}? Reply YYYY-MM-DD or use a button."
    return f"❓ What time for{name}? Reply HH:MM or use a button."


def _question_keyboard(field: str, reference) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if field == "date":
        today, tomorrow = reference, reference + timedelta(days=1)
        rows.append([
            InlineKeyboardButton(
                text=f"Today · {today:%a %d %b}",
                callback_data=f"ans:date:{today.isoformat()}",
            ),
            InlineKeyboardButton(
                text=f"Tomorrow · {tomorrow:%a %d %b}",
                callback_data=f"ans:date:{tomorrow.isoformat()}",
            ),
        ])
    elif field == "time":
        rows.append([
            InlineKeyboardButton(text="09:00", callback_data="ans:time:09:00"),
            InlineKeyboardButton(text="12:00", callback_data="ans:time:12:00"),
            InlineKeyboardButton(text="18:00", callback_data="ans:time:18:00"),
        ])
        rows.append([
            InlineKeyboardButton(text="🌙 All day", callback_data="ans:time:all day"),
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
    message: Message,
    bot,
    drafts,
    user_id,
    raw,
    resolved,
    error,
    *,
    reference: datetime | None = None,
    record_path: Path | None = None,
    original_text: str | None = None,
    show_raw=False,
    config=None,
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
        if resolved.get("operation") != "create":
            await _deliver(message, bot, draft, text, None)
            drafts.pop(user_id, None)
            return
        if config is not None and record_path is not None and oauth_is_configured(config):
            try:
                token = load_token(config.data_dir, user_id, config.calendar_token_encryption_key)
            except CalendarAuthError:
                token = None
            if not token_is_usable(token):
                markup = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="Reconnect Google Calendar" if token else "Connect Google Calendar",
                        callback_data="connect_calendar"
                    )
                ]])
                await _deliver(
                    message, bot, draft,
                    text + ("\n\nReconnect required." if token else ""), markup,
                )
                drafts.pop(user_id, None)
                return
            try:
                payload = build_event(
                    resolved, new_google_event_id(), config.calendar_timezone
                )
                write = create_write(
                    config.data_dir, user_id, _source_record(record_path, config), payload,
                    connection=connection_generation(config.data_dir, user_id),
                )
            except CalendarPayloadError as error:
                await _deliver(
                    message, bot, draft, text + f"\n\n⚠️ Calendar payload error: {escape(str(error))}", None
                )
                drafts.pop(user_id, None)
                return
            if not config.calendar_write_enabled:
                write = update_write(config.data_dir, write["write_id"], status="shadowed")
                await _deliver(
                    message,
                    bot,
                    draft,
                    _calendar_text(write["payload"], "🕶️ Shadowed: Google writes are disabled."),
                    None,
                )
                drafts.pop(user_id, None)
                return
            if resolved.get("auto_write"):
                write = claim_write(config.data_dir, write["write_id"])
                if write is None:
                    raise CalendarPayloadError("Calendar write could not be started")
                write = await _execute_calendar_write(config, write)
                if write["status"] == "created":
                    status = "✅ Created"
                else:
                    status = "⚠️ Google Calendar creation failed. Confirm to retry."
                await _deliver(
                    message, bot, draft,
                    _calendar_text(write["payload"], status, write.get("google_html_link")),
                    None if write["status"] == "created" else _calendar_markup(
                        write["write_id"], resolved.get("all_day", False), retry=True
                    ),
                )
                drafts.pop(user_id, None)
                return
            await _deliver(
                message,
                bot,
                draft,
                text,
                _calendar_markup(write["write_id"], resolved.get("all_day", False)),
            )
            drafts.pop(user_id, None)
            return
        await _deliver(message, bot, draft, text, None)
        drafts.pop(user_id, None)
        return

    if draft is None:
        if reference is None or record_path is None or original_text is None:
            raise ValueError("new draft requires request context")
        draft = Draft(
            raw=deepcopy(raw),
            evidence=[original_text],
            reference=reference,
            awaiting=field,
            record_path=record_path,
            timezone=config.calendar_timezone if config is not None else "Europe/Chisinau",
        )
        drafts[user_id] = draft
    elif draft.attempts >= 6:
        await _deliver(
            message,
            bot,
            draft,
            "⚠️ Too many clarification attempts. Send a fresh complete request.",
            None,
        )
        _update_record(draft.record_path, clarification_status="exhausted")
        drafts.pop(user_id, None)
        return
    text = f"{format_resolved(resolved)}\n\n{_question_text(field, resolved)}"
    await _deliver(
        message,
        bot,
        draft,
        text,
        _question_keyboard(field, draft.reference.date()),
    )


@router.callback_query(F.data == "q_cancel")
async def cancel_draft(callback: CallbackQuery, drafts) -> None:
    draft = drafts.pop(callback.from_user.id, None)
    if draft is not None:
        _update_record(draft.record_path, clarification_status="cancelled")
    await callback.answer("Cancelled")
    await callback.message.edit_text("❌ Draft cancelled.")


async def _answer_draft(
    message, bot, drafts, user_id: int, text: str, *, show_raw=False, config=None
) -> None:
    draft = drafts.get(user_id)
    if draft is None:
        await message.answer("This question is no longer active.")
        return
    try:
        resolved = apply_answer(draft, text)
    except ValueError as error:
        draft.attempts += 1
        resolved = semantic.resolve(
            draft.raw, "\n".join(draft.evidence), draft.reference, ZoneInfo(draft.timezone)
        )
        _update_record(
            draft.record_path,
            clarification_status=f"awaiting_{draft.awaiting}",
            last_clarification_error=str(error),
        )
        prompt = (
            f"{format_resolved(resolved)}\n\n⚠️ {escape(str(error))}\n\n"
            f"{_question_text(draft.awaiting, resolved)}"
        )
        await _deliver(
            message,
            bot,
            draft,
            prompt,
            _question_keyboard(draft.awaiting, draft.reference.date()),
        )
        if draft.attempts >= 6:
            _update_record(draft.record_path, clarification_status="exhausted")
            drafts.pop(user_id, None)
        return

    _update_record(
        draft.record_path,
        clarification_answers=draft.evidence[1:],
        effective_extraction=draft.raw,
        resolved=resolved,
        clarification_status=(
            "completed" if next_field(resolved) is None else f"awaiting_{draft.awaiting}"
        ),
        last_clarification_error=None,
    )
    await reply_event(
        message, bot, drafts, user_id, draft.raw, resolved, None,
        record_path=draft.record_path, show_raw=show_raw, config=config,
    )


@router.callback_query(F.data.startswith("ans:"))
async def answer_field(callback: CallbackQuery, bot: Bot, drafts, debug: bool, config) -> None:
    user_id = callback.from_user.id
    draft = drafts.get(user_id)
    _, field, value = callback.data.split(":", 2)
    if draft is None or field != draft.awaiting:
        await callback.answer("This question is no longer active.", show_alert=True)
        return
    await callback.answer()
    await _answer_draft(
        callback.message, bot, drafts, user_id, value, show_raw=debug, config=config
    )


def _callback_write(config, callback: CallbackQuery, prefix: str) -> dict | None:
    write_id = callback.data.removeprefix(prefix).split(":", 1)[0]
    try:
        write = load_write(config.data_dir, write_id)
    except CalendarPayloadError:
        return None
    if write is None or write.get("telegram_user_id") != callback.from_user.id:
        return None
    return write


@router.callback_query(F.data.startswith("confirm:"))
async def confirm_calendar_write(callback: CallbackQuery, config) -> None:
    write = _callback_write(config, callback, "confirm:")
    if write is None:
        await callback.answer("This Calendar action is no longer available.", show_alert=True)
        return
    if write["status"] == "created":
        await callback.answer("Already created.")
        return
    if not config.calendar_write_enabled:
        await callback.answer("Google writes are disabled.", show_alert=True)
        await callback.message.edit_text(
            _calendar_text(write["payload"], "🕶️ Google writes are disabled."),
            reply_markup=_calendar_markup(
                write["write_id"], "date" in write["payload"]["start"], retry=True
            ),
        )
        return
    write = claim_write(config.data_dir, write["write_id"])
    if write is None:
        await callback.answer("This Calendar action is no longer available.", show_alert=True)
        return
    await callback.answer()
    write = await _execute_calendar_write(config, write)
    if write["status"] == "created":
        text = _calendar_text(write["payload"], "✅ Created", write.get("google_html_link"))
    else:
        text = _calendar_text(write["payload"], "⚠️ Google Calendar creation failed. Confirm to retry.")
    await callback.message.edit_text(
        text,
        reply_markup=(
            None if write["status"] == "created"
            else _calendar_markup(
                write["write_id"], "date" in write["payload"]["start"], retry=True
            )
        ),
    )


@router.callback_query(F.data.startswith("edit:"))
async def start_calendar_edit(callback: CallbackQuery, config) -> None:
    write = _callback_write(config, callback, "edit:")
    if write is None or write["status"] != "pending":
        await callback.answer("This Calendar action is no longer available.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        _calendar_text(write["payload"], "Choose a field to edit."),
        reply_markup=_edit_fields_markup(
            write["write_id"], "date" in write["payload"]["start"]
        ),
    )


@router.callback_query(F.data.startswith("edit_field:"))
async def choose_calendar_edit(callback: CallbackQuery, config, calendar_edits) -> None:
    try:
        _, write_id, field = callback.data.split(":", 2)
    except ValueError:
        await callback.answer("Invalid Calendar action.", show_alert=True)
        return
    write = _callback_write(config, callback, "edit_field:")
    if write is None or write["status"] != "pending" or field not in {"title", "date", "time"}:
        await callback.answer("This Calendar action is no longer available.", show_alert=True)
        return
    if field == "time" and "date" in write["payload"]["start"]:
        await callback.answer("All-day event time cannot be edited.", show_alert=True)
        return
    calendar_edits[callback.from_user.id] = {
        "write_id": write_id,
        "field": field,
        "chat_id": callback.message.chat.id,
        "message_id": callback.message.message_id,
    }
    prompts = {
        "title": "Reply with a title.",
        "date": "Reply YYYY-MM-DD.",
        "time": "Reply HH:MM (24-hour time).",
    }
    await callback.answer()
    await callback.message.edit_text(
        _calendar_text(write["payload"], prompts[field]),
        reply_markup=_edit_fields_markup(write_id, "date" in write["payload"]["start"]),
    )


@router.callback_query(F.data.startswith("edit_back:"))
async def cancel_calendar_edit_selection(callback: CallbackQuery, config, calendar_edits) -> None:
    write = _callback_write(config, callback, "edit_back:")
    if write is None or write["status"] != "pending":
        await callback.answer("This Calendar action is no longer available.", show_alert=True)
        return
    calendar_edits.pop(callback.from_user.id, None)
    await callback.answer()
    await callback.message.edit_text(
        _calendar_text(write["payload"], "Ready to confirm."),
        reply_markup=_calendar_markup(
            write["write_id"], "date" in write["payload"]["start"]
        ),
    )


@router.callback_query(F.data.startswith("cancel:"))
async def cancel_calendar_write(callback: CallbackQuery, config, calendar_edits) -> None:
    write = _callback_write(config, callback, "cancel:")
    if write is None or write["status"] != "pending":
        await callback.answer("This Calendar action is no longer available.", show_alert=True)
        return
    calendar_edits.pop(callback.from_user.id, None)
    write = update_write(config.data_dir, write["write_id"], status="cancelled")
    await callback.answer("Cancelled")
    await callback.message.edit_text(_calendar_text(write["payload"], "❌ Cancelled"))


async def _answer_calendar_edit(message, bot, calendar_edits, config) -> None:
    pending = calendar_edits.get(message.from_user.id)
    if pending is None or config is None:
        return
    try:
        write = load_write(config.data_dir, pending["write_id"])
        if (
            write is None or write["telegram_user_id"] != message.from_user.id
            or write["status"] != "pending"
        ):
            calendar_edits.pop(message.from_user.id, None)
            raise CalendarPayloadError("Calendar write was not found")
        payload = edit_payload(
            write["payload"], pending["field"], message.text, config.calendar_timezone
        )
        write = replace_write_payload(config.data_dir, write["write_id"], payload)
    except CalendarPayloadError as error:
        await message.answer(f"⚠️ {escape(str(error))}")
        return
    calendar_edits.pop(message.from_user.id, None)
    await bot.edit_message_text(
        chat_id=pending["chat_id"],
        message_id=pending["message_id"],
        text=_calendar_text(write["payload"], "Updated. Confirm when ready."),
        reply_markup=_calendar_markup(
            write["write_id"], "date" in write["payload"]["start"]
        ),
    )



@router.message(F.voice)
async def handle_voice(
    message: Message,
    bot: Bot,
    drafts,
    elevenlabs_api_key: str,
    elevenlabs_model: str,
    deepseek_api_key: str,
    extraction_model: str,
    extraction_timeout: int,
    voice_root: Path,
    voice_retention_hours: int,
    debug: bool,
    config=None,
    calendar_edits=None,
) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if drafts.get(user_id) is not None:
        await message.answer("Please answer with text or the provided buttons; voice replies are not accepted.")
        return
    if calendar_edits and user_id in calendar_edits:
        await message.answer("Please reply with text while editing a Calendar event.")
        return

    voice = message.voice
    if voice is None or voice.file_size is None or voice.file_size <= 0:
        await message.answer("Voice message has no usable audio data.")
        return

    if voice.file_size > MAX_VOICE_BYTES:
        await message.answer("Voice message exceeds the 2 MiB limit.")
        return

    received_at = datetime.now(ZoneInfo(config.calendar_timezone) if config else CHISINAU)
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
        "extraction_model": extraction_model,
        "contract_hash": semantic.contract_hash(),
        "extraction": None,
        "effective_extraction": None,
        "resolved": None,
        "extraction_wait_time_seconds": None,
        "extraction_error": None,
        "clarification_answers": [],
        "clarification_status": None,
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
            received_at,
            deepseek_api_key=deepseek_api_key,
            extraction_model=extraction_model,
            extraction_timeout=extraction_timeout,
            zone=ZoneInfo(config.calendar_timezone) if config else semantic.ZONE,
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
            extraction_wait_time_seconds=extraction_seconds,
            extraction_error=extraction_error,
            clarification_status=(
                f"awaiting_{next_field(resolved)}"
                if resolved is not None and next_field(resolved) is not None
                else None
            ),
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
            message,
            bot,
            drafts,
            user_id,
            extraction_raw,
            resolved,
            extraction_error,
            reference=received_at,
            record_path=record_path,
            original_text=result.text,
            show_raw=debug,
            config=config,
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
    deepseek_api_key: str,
    extraction_model: str,
    extraction_timeout: int,
    text_root: Path,
    voice_retention_hours: int,
    debug: bool,
    config=None,
    calendar_edits=None,
) -> None:
    user_id = message.from_user.id if message.from_user else 0

    if calendar_edits is not None and user_id in calendar_edits:
        await _answer_calendar_edit(message, bot, calendar_edits, config)
        return

    draft = drafts.get(user_id)
    if draft is not None:
        await _answer_draft(
            message, bot, drafts, user_id, message.text, show_raw=debug, config=config
        )
        return

    received_at = datetime.now(ZoneInfo(config.calendar_timezone) if config else CHISINAU)
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
        "extraction_model": extraction_model,
        "contract_hash": semantic.contract_hash(),
        "extraction": None,
        "effective_extraction": None,
        "resolved": None,
        "extraction_wait_time_seconds": None,
        "extraction_error": None,
        "clarification_answers": [],
        "clarification_status": None,
        "error": None,
    }
    _write_record(record_path, record)

    try:
        extraction_raw, resolved, extraction_seconds, extraction_error = await run_extraction(
            message.text,
            received_at,
            deepseek_api_key=deepseek_api_key,
            extraction_model=extraction_model,
            extraction_timeout=extraction_timeout,
            zone=ZoneInfo(config.calendar_timezone) if config else semantic.ZONE,
        )
        record.update(
            status="completed",
            extraction=extraction_raw,
            resolved=resolved,
            extraction_wait_time_seconds=extraction_seconds,
            extraction_error=extraction_error,
            clarification_status=(
                f"awaiting_{next_field(resolved)}"
                if resolved is not None and next_field(resolved) is not None
                else None
            ),
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
            message,
            bot,
            drafts,
            user_id,
            extraction_raw,
            resolved,
            extraction_error,
            reference=received_at,
            record_path=record_path,
            original_text=message.text,
            show_raw=debug,
            config=config,
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
