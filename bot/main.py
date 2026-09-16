import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .config import Config, load_config
from .handlers import admin, start, voice
from .logging_config import configure_logging
from .middleware import AllowlistMiddleware
from .storage import Storage


log = logging.getLogger(__name__)


async def main_async(config: Config) -> None:
    storage = Storage(config.data_dir, config.owner_id)
    voice_dir = config.data_dir / "voice"
    voice_queue: asyncio.Queue[voice.VoiceJob] = asyncio.Queue()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dispatcher = Dispatcher()
    dispatcher.message.middleware(AllowlistMiddleware(storage))
    dispatcher.callback_query.middleware(AllowlistMiddleware(storage))
    dispatcher.include_router(admin.router)
    dispatcher.include_router(start.router)
    dispatcher.include_router(voice.router)

    voice_worker = asyncio.create_task(voice.download_worker(voice_queue, voice_dir))
    cleanup_worker = asyncio.create_task(voice.cleanup_loop(voice_dir))

    try:
        log.info("Bot starting owner=%s", config.owner_id)
        await dispatcher.start_polling(
            bot,
            storage=storage,
            voice_queue=voice_queue,
        )
    finally:
        log.info("Bot stopping")
        try:
            await asyncio.wait_for(voice_queue.join(), timeout=30)
        except TimeoutError:
            log.warning("Voice queue did not drain before shutdown")
        voice_worker.cancel()
        cleanup_worker.cancel()
        await asyncio.gather(voice_worker, cleanup_worker, return_exceptions=True)
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
