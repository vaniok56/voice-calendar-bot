from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from ..storage import Storage


router = Router(name="start")

START_TEXT = (
    "🎙️ <b>Voice Calendar Bot</b>\n\n"
    "Send a voice message or plain text to build a calendar event.\n\n"
    "Voice is transcribed with <b>ElevenLabs Scribe v2</b>, then a model extracts "
    "the event fields and deterministic code resolves the date, time, duration, and reminders.\n\n"
    "Supported languages: Romanian, Russian, English, and code-switched speech.\n\n"
    "Voice format: OGG/Opus\n"
    "Maximum voice size: 2 MiB\n"
    "Retention: up to 7 days\n\n"
    "/start - show this message\n"
    "/help - show this message\n"
    "/settings - manage Google Calendar connection and view timezone"
)


def start_text_for(user_id: int, storage: Storage) -> str:
    if storage.is_admin(user_id):
        return START_TEXT + "\n/admin_help - administration commands"
    return START_TEXT


@router.message(CommandStart())
@router.message(Command("help"))
async def start(message: Message, storage: Storage) -> None:
    user_id = message.from_user.id if message.from_user else 0
    await message.answer(start_text_for(user_id, storage))
