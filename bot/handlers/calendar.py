from html import escape
from urllib.parse import parse_qs, urlsplit

from aiohttp import web
from aiogram import F, Bot, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
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
        [InlineKeyboardButton(text="Back", callback_data=f"cancel_connect:{state}")],
    ])


def _settings_view(user_id: int, config) -> tuple[str, InlineKeyboardMarkup | None]:
    if not oauth_is_configured(config):
        return f"Google Calendar is not configured.\nTimezone: {escape(config.calendar_timezone)}", None
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
    return (
        f"Google Calendar: {status}\nTimezone: {escape(config.calendar_timezone)}",
        _settings_markup(status),
    )


@router.callback_query(F.data == "connect_calendar")
async def connect_calendar_button(callback: CallbackQuery, config, settings_messages: dict) -> None:
    try:
        url = create_authorization_url(config, callback.from_user.id)
    except CalendarAuthError:
        await callback.answer("Google Calendar connection is not configured.", show_alert=True)
        return
    await callback.answer()
    text = "Connect Google Calendar with the button below. It expires in 10 minutes."
    markup = _connect_markup(url)
    if callback.message.text and callback.message.text.startswith("Google Calendar:"):
        await callback.message.edit_text(text, reply_markup=markup)
        settings_messages[callback.from_user.id] = (
            callback.message.chat.id, callback.message.message_id
        )
    else:
        sent = await callback.message.answer(text, reply_markup=markup)
        settings_messages[callback.from_user.id] = (sent.chat.id, sent.message_id)


@router.callback_query(F.data.startswith("cancel_connect:"))
async def cancel_connect(callback: CallbackQuery, config, settings_messages: dict) -> None:
    state = callback.data.removeprefix("cancel_connect:")
    if not cancel_oauth_state(config.data_dir, state, callback.from_user.id):
        await callback.answer("Cannot cancel this connection.", show_alert=True)
        return
    text, markup = _settings_view(callback.from_user.id, config)
    await callback.message.edit_text(text, reply_markup=markup)
    settings_messages[callback.from_user.id] = (
        callback.message.chat.id, callback.message.message_id
    )
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
async def disconnect_calendar_button(callback: CallbackQuery, config, settings_messages: dict) -> None:
    removed = await disconnect_and_revoke(config, callback.from_user.id)
    await callback.answer("Google Calendar disconnected." if removed else "No connection found.")
    text, markup = _settings_view(callback.from_user.id, config)
    await callback.message.edit_text(text, reply_markup=markup)
    settings_messages[callback.from_user.id] = (
        callback.message.chat.id, callback.message.message_id
    )


@router.message(Command("settings"))
async def settings(message: Message, bot: Bot, config, settings_messages: dict) -> None:
    user_id = message.from_user.id if message.from_user else 0
    text, markup = _settings_view(user_id, config)
    if user_id in settings_messages:
        chat_id, message_id = settings_messages[user_id]
        try:
            await bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=markup
            )
            return
        except TelegramBadRequest as error:
            if "message is not modified" in str(error):
                return
            settings_messages.pop(user_id, None)
    sent = await message.answer(text, reply_markup=markup)
    settings_messages[user_id] = (sent.chat.id, sent.message_id)


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
    messages = request.app.get("settings_messages")
    bot = request.app.get("bot")
    if messages is not None and bot is not None and pending["telegram_user_id"] in messages:
        chat_id, message_id = messages[pending["telegram_user_id"]]
        text, markup = _settings_view(pending["telegram_user_id"], config)
        try:
            await bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=markup
            )
        except TelegramAPIError:
            messages.pop(pending["telegram_user_id"], None)
    return web.Response(text="Google Calendar connected. Return to Telegram.")


def callback_app(config, storage, bot: Bot, settings_messages: dict) -> web.Application:
    app = web.Application()
    app["config"] = config
    app["storage"] = storage
    app["bot"] = bot
    app["settings_messages"] = settings_messages
    app.router.add_get("/google/callback", google_callback)
    return app
