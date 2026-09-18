import json
import logging
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from aiogram import F, Bot, Router
from aiogram.enums import ChatAction
from aiogram.types import Message

from ..asr import format_duration, ogg_duration_seconds, transcribe_voice
from ..logging_config import CHISINAU


router = Router(name="voice")
log = logging.getLogger(__name__)

MAX_VOICE_BYTES = 2 * 1024 * 1024  # 2 MiB
CREDITS_PER_AUDIO_SECOND = 10 / 9


def _write_record(path: Path, record: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)


@router.message(F.voice)
async def handle_voice(
    message: Message,
    bot: Bot,
    elevenlabs_api_key: str,
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
        "error": None,
    }
    _write_record(record_path, record)

    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        await bot.download(voice, destination=audio_path)

        result = await transcribe_voice(audio_path, api_key=elevenlabs_api_key)

        record.update(
            status="completed",
            transcript=result.text,
            language_code=result.language_code,
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
        await message.answer(
            f"⏱️ <b>Wait time:</b> {result.wait_time_seconds:.2f}s\n"
            f"🌐 <b>Language:</b> {escape(result.language_code or 'unknown')}\n"
            f"🎙️ <b>Audio length:</b> {escape(format_duration(voice.duration))}\n"
            f"💳 <b>Cost:</b> ~{estimated_credits} credits"
        )
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
