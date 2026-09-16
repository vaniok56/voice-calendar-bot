from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from ..storage import Storage


router = Router(name="start")

START_TEXT = "Bot is running."


def start_text_for(user_id: int, storage: Storage) -> str:
    if storage.is_admin(user_id):
        return START_TEXT + "\n/admin_help - administration commands"
    return START_TEXT


@router.message(CommandStart())
async def start(message: Message, storage: Storage) -> None:
    user_id = message.from_user.id if message.from_user else 0
    await message.answer(start_text_for(user_id, storage))
