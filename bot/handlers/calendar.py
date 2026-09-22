from aiohttp import web
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ..calendar import (
    CalendarAuthError,
    consume_oauth_state,
    create_authorization_url,
    disconnect,
    exchange_code,
    save_token,
)


router = Router(name="calendar")


@router.message(Command("connect_calendar"))
async def connect_calendar(message: Message, config) -> None:
    user_id = message.from_user.id if message.from_user else 0
    try:
        url = create_authorization_url(config, user_id)
    except CalendarAuthError:
        await message.answer("Google Calendar connection is not configured.")
        return
    await message.answer(
        "Open this link to connect Google Calendar. It expires in 10 minutes:\n" + url,
        disable_web_page_preview=True,
    )


@router.message(Command("disconnect_calendar"))
async def disconnect_calendar(message: Message, config) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if disconnect(config.data_dir, user_id):
        await message.answer("Google Calendar disconnected.")
    else:
        await message.answer("No Google Calendar connection found.")


async def google_callback(request: web.Request) -> web.Response:
    config = request.app["config"]
    state = request.query.get("state")
    if not state:
        return web.Response(text="Google Calendar connection could not be completed.", status=400)
    try:
        pending = consume_oauth_state(config.data_dir, state)
        if request.query.get("error"):
            return web.Response(text="Google Calendar connection was not completed.", status=400)
        token = await exchange_code(config, request.query.get("code", ""), pending["code_verifier"])
        save_token(config.data_dir, pending["telegram_user_id"], token)
    except CalendarAuthError:
        return web.Response(text="Google Calendar connection could not be completed.", status=400)
    return web.Response(text="Google Calendar connected. Return to Telegram.")


def callback_app(config) -> web.Application:
    app = web.Application()
    app["config"] = config
    app.router.add_get("/google/callback", google_callback)
    return app
