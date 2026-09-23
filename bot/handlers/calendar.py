from html import escape
from urllib.parse import parse_qs, urlsplit

from aiohttp import web
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..calendar import (
    CalendarAuthError,
    advance_connection,
    cancel_oauth_state,
    connection_generation,
    consume_oauth_state,
    create_authorization_url,
    disconnect_and_revoke,
    exchange_code,
    fetch_account_email,
    load_token,
    oauth_is_configured,
    pending_connection,
    save_token,
    token_is_usable,
)


router = Router(name="calendar")


def _connect_markup(url: str) -> InlineKeyboardMarkup:
    state = parse_qs(urlsplit(url).query)["state"][0]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Connect Google Calendar", url=url)],
        [InlineKeyboardButton(text="Cancel", callback_data=f"cancel_connect:{state}")],
    ])


@router.callback_query(F.data == "connect_calendar")
async def connect_calendar_button(callback: CallbackQuery, config) -> None:
    try:
        url = create_authorization_url(config, callback.from_user.id)
    except CalendarAuthError:
        await callback.answer("Google Calendar connection is not configured.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        "Connect Google Calendar with the button below. It expires in 10 minutes.",
        reply_markup=_connect_markup(url),
    )


@router.callback_query(F.data.startswith("cancel_connect:"))
async def cancel_connect(callback: CallbackQuery, config) -> None:
    state = callback.data.removeprefix("cancel_connect:")
    if not cancel_oauth_state(config.data_dir, state, callback.from_user.id):
        await callback.answer("Cannot cancel this connection.", show_alert=True)
        return
    await callback.message.delete()
    await callback.answer()


def _settings_markup(status: str) -> InlineKeyboardMarkup:
    label = "Switch account" if status.startswith("Connected:") else (
        "Reconnect" if status == "Reconnect required" else "Connect"
    )
    rows = [[InlineKeyboardButton(text=label, callback_data="connect_calendar")]]
    if status != "Not connected":
        rows.append([InlineKeyboardButton(text="Disconnect", callback_data="disconnect_calendar")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "disconnect_calendar")
async def disconnect_calendar_button(callback: CallbackQuery, config) -> None:
    removed = await disconnect_and_revoke(config, callback.from_user.id)
    await callback.answer("Google Calendar disconnected." if removed else "No connection found.")
    await callback.message.edit_text(
        "Google Calendar: Not connected\n"
        f"Timezone: {escape(config.calendar_timezone)}",
        reply_markup=_settings_markup("Not connected"),
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
    await message.answer(
        f"Google Calendar: {status}\nTimezone: {escape(config.calendar_timezone)}",
        reply_markup=_settings_markup(status),
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
            or connection_generation(config.data_dir, pending["telegram_user_id"]) + 1
            != pending["connection_generation"]
            or not pending_connection(config.data_dir, pending["telegram_user_id"], state)
        ):
            return web.Response(text="Google Calendar connection could not be completed.", status=400)
        advance_connection(config.data_dir, pending["telegram_user_id"])
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
