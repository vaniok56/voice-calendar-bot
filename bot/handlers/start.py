from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..calendar import oauth_is_configured
from ..storage import Storage


router = Router(name="start")

START_TEXT = (
    "🎙️ <b>Voice Calendar Bot</b>\n\n"
    "Send a voice note or text and I'll add it to your Google Calendar. "
    "I work out the date, time, duration and reminders, and ask if anything is missing.\n\n"
    "Understands Romanian, Russian, English, and mixed speech.\n"
    "Voice: OGG/Opus, up to 2 MiB. Records kept up to 7 days.\n\n"
    "/start · /help - this message\n"
    "/settings - Google Calendar, automatic writes, timezone, event types"
)


def start_text_for(user_id: int, storage: Storage) -> str:
    if storage.is_admin(user_id):
        return START_TEXT + "\n/admin_help - administration commands"
    return START_TEXT


def start_markup(config) -> InlineKeyboardMarkup | None:
    if not oauth_is_configured(config):
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Connect Google Calendar", callback_data="connect_calendar"),
    ]])


@router.message(CommandStart())
@router.message(Command("help"))
async def start(message: Message, storage: Storage, config) -> None:
    user_id = message.from_user.id if message.from_user else 0
    await message.answer(start_text_for(user_id, storage), reply_markup=start_markup(config))
