import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, User

from .storage import Storage


log = logging.getLogger(__name__)


class AllowlistMiddleware(BaseMiddleware):
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self._refused: set[int] = set()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        if self.storage.is_allowed(user.id):
            if isinstance(event, Message) and event.text and event.text.startswith("/"):
                command = event.text.split(maxsplit=1)[0].split("@", 1)[0]
                log.info("%s - %s", user.id, command)
            return await handler(event, data)

        if user.id not in self._refused and isinstance(event, Message):
            self._refused.add(user.id)
            log.info("Blocked Telegram user %s", user.id)
            await event.answer(
                "This bot is private. Send this ID to its owner: "
                f"<code>{user.id}</code>"
            )
        return None
