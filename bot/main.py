import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .config import Config, load_config
from .handlers import admin, start
from .logging_config import configure_logging
from .middleware import AllowlistMiddleware
from .storage import Storage


log = logging.getLogger(__name__)


async def main_async(config: Config) -> None:
    storage = Storage(config.data_dir, config.owner_id)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dispatcher = Dispatcher()
    dispatcher.message.middleware(AllowlistMiddleware(storage))
    dispatcher.callback_query.middleware(AllowlistMiddleware(storage))
    dispatcher.include_router(admin.router)
    dispatcher.include_router(start.router)

    try:
        log.info("Bot starting owner=%s", config.owner_id)
        await dispatcher.start_polling(
            bot,
            storage=storage,
        )
    finally:
        log.info("Bot stopping")
        await bot.session.close()


def main() -> None:
    os.umask(0o077)
    config = load_config()
    configure_logging(
        config.log_dir,
        config.log_level,
        config.log_retention_days,
    )
    asyncio.run(main_async(config))


if __name__ == "__main__":
    main()
