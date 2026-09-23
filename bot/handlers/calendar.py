from html import escape

from aiohttp import web
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..calendar import (
    CalendarAuthError,
    connection_generation,
    consume_oauth_state,
    create_authorization_url,
    disconnect_and_revoke,
    exchange_code,
    fetch_account_email,
    load_token,
    oauth_is_configured,
    save_token,
    token_is_usable,
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
    if await disconnect_and_revoke(config, user_id):
        await message.answer("Google Calendar disconnected.")
    else:
        await message.answer("No Google Calendar connection found.")


@router.callback_query(F.data == "disconnect_calendar")
async def disconnect_calendar_button(callback: CallbackQuery, config) -> None:
    removed = await disconnect_and_revoke(config, callback.from_user.id)
    await callback.answer("Google Calendar disconnected." if removed else "No connection found.")
    await callback.message.edit_text(
        "Google Calendar disconnected." if removed else "No Google Calendar connection found."
    )


@router.message(Command("settings"))
async def settings(message: Message, config) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not oauth_is_configured(config):
        await message.answer(
            f"Google Calendar is not configured.\nTimezone: {escape(config.calendar_timezone)}"
        )
        return
    token = None
    try:
        token = load_token(config.data_dir, user_id, config.calendar_token_encryption_key)
        if (
            token_is_usable(token)
            and token["connection_generation"] == connection_generation(config.data_dir, user_id)
        ):
            status = f"Connected: {escape(token['account_email'])}"
        elif token is not None:
            status = "Reconnect required"
        else:
            status = "Not connected"
    except (CalendarAuthError, KeyError, TypeError):
        status = "Reconnect required"
    rows = [[InlineKeyboardButton(text="Connect / Reconnect", callback_data="connect_calendar")]]
    if token is not None or status == "Reconnect required":
        rows.append([InlineKeyboardButton(text="Disconnect", callback_data="disconnect_calendar")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer(
        f"Google Calendar: {status}\nTimezone: {escape(config.calendar_timezone)}\n"
        "Use /disconnect_calendar to disconnect.",
        reply_markup=keyboard,
    )


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
        account_email = await fetch_account_email(token["access_token"])
        if (
            not storage.is_allowed(pending["telegram_user_id"])
            or connection_generation(config.data_dir, pending["telegram_user_id"])
            != pending["connection_generation"]
        ):
            return web.Response(text="Google Calendar connection could not be completed.", status=400)
        save_token(config.data_dir, pending["telegram_user_id"], {
            **token, "account_email": account_email, "schema_version": 2,
            "connection_generation": pending["connection_generation"],
        }, config.calendar_token_encryption_key)
    except CalendarAuthError:
        return web.Response(text="Google Calendar connection could not be completed.", status=400)
    return web.Response(text="Google Calendar connected. Return to Telegram.")


def callback_app(config, storage) -> web.Application:
    app = web.Application()
    app["config"] = config
    app["storage"] = storage
    app.router.add_get("/google/callback", google_callback)
    return app
