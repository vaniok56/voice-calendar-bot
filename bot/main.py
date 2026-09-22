import asyncio
import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from . import calendar as calendar_service
from .config import Config, load_config
from .handlers import admin, calendar, start, voice
from .logging_config import configure_logging
from .middleware import AllowlistMiddleware
from .retention import retention_loop
from .storage import Storage


log = logging.getLogger(__name__)


async def main_async(config: Config) -> None:
    storage = Storage(config.data_dir, config.owner_id)

    voice_root = config.data_dir / "voice"
    voice_root.mkdir(mode=0o700, parents=True, exist_ok=True)

    text_root = config.data_dir / "text"
    text_root.mkdir(mode=0o700, parents=True, exist_ok=True)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dispatcher = Dispatcher()
    dispatcher.message.middleware(AllowlistMiddleware(storage))
    dispatcher.callback_query.middleware(AllowlistMiddleware(storage))
    dispatcher.include_router(admin.router)
    dispatcher.include_router(calendar.router)
    dispatcher.include_router(start.router)
    dispatcher.include_router(voice.router)

    cleanup_task = asyncio.create_task(
        retention_loop(voice_root, config.voice_cleanup_interval_seconds)
    )
    text_cleanup_task = asyncio.create_task(
        retention_loop(text_root, config.voice_cleanup_interval_seconds)
    )
    callback_runner = None

    try:
        if calendar_service.oauth_is_configured(config):
            callback_runner = web.AppRunner(calendar.callback_app(config))
            await callback_runner.setup()
            await web.TCPSite(
                callback_runner, "0.0.0.0", config.calendar_callback_port
            ).start()
        log.info(
            "Bot starting owner=%s model=%s extraction=%s",
            config.owner_id,
            config.elevenlabs_model,
            config.extraction_model,
        )
        await dispatcher.start_polling(
            bot,
            storage=storage,
            drafts={},
            calendar_edits={},
            elevenlabs_api_key=config.elevenlabs_api_key,
            elevenlabs_model=config.elevenlabs_model,
            deepseek_api_key=config.deepseek_api_key,
            extraction_model=config.extraction_model,
            extraction_timeout=config.extraction_timeout,
            voice_root=voice_root,
            text_root=text_root,
            voice_retention_hours=config.voice_retention_hours,
            debug=config.debug,
            config=config,
        )
    finally:
        log.info("Bot stopping")
        cleanup_task.cancel()
        text_cleanup_task.cancel()
        await asyncio.gather(cleanup_task, text_cleanup_task, return_exceptions=True)
        if callback_runner is not None:
            await callback_runner.cleanup()
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
