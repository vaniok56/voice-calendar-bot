from aiohttp import web
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..calendar import (
    CalendarAuthError,
    connection_generation,
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


@router.callback_query(F.data == "connect_calendar")
async def connect_calendar_button(callback: CallbackQuery, config) -> None:
    try:
        url = create_authorization_url(config, callback.from_user.id)
    except CalendarAuthError:
        await callback.answer("Google Calendar connection is not configured.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
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
    storage = request.app["storage"]
    state = request.query.get("state")
    if not state:
        return web.Response(text="Google Calendar connection could not be completed.", status=400)
    try:
        pending = consume_oauth_state(config.data_dir, state)
        if not storage.is_allowed(pending["telegram_user_id"]):
            return web.Response(text="Google Calendar connection could not be completed.", status=400)
        if request.query.get("error"):
            return web.Response(text="Google Calendar connection was not completed.", status=400)
        token = await exchange_code(config, request.query.get("code", ""), pending["code_verifier"])
        if (
            not storage.is_allowed(pending["telegram_user_id"])
            or connection_generation(config.data_dir, pending["telegram_user_id"])
            != pending["connection_generation"]
        ):
            return web.Response(text="Google Calendar connection could not be completed.", status=400)
        save_token(config.data_dir, pending["telegram_user_id"], {
            **token, "connection_generation": pending["connection_generation"],
        })
    except CalendarAuthError:
        return web.Response(text="Google Calendar connection could not be completed.", status=400)
    return web.Response(text="Google Calendar connected. Return to Telegram.")


def callback_app(config, storage) -> web.Application:
    app = web.Application()
    app["config"] = config
    app["storage"] = storage
    app.router.add_get("/google/callback", google_callback)
    return app
