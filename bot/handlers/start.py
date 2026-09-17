from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from ..storage import Storage


router = Router(name="start")

START_TEXT = (
    "Send a voice message to store it automatically.\n\n"
    "Format: OGG/Opus\n"
    "Maximum size: 2 MiB\n"
    "Retention: up to 7 days\n\n"
    "After upload, reply with the corpus ID and recording notes.\n\n"
    "/start - show this message\n"
    "/help - show this message"
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
