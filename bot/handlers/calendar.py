from html import escape
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web
from aiogram import F, Bot, Router
from aiogram.exceptions import TelegramAPIError
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
from .. import semantic
from ..profile import (
    ProfileConflict, ProfileError, clean_label, label_key, load_profile, new_custom_type,
    save_profile,
)


router = Router(name="calendar")


def _remember_card(settings_edits: dict | None, user_id: int, chat_id: int, message_id: int, config) -> None:
    if settings_edits is None:
        return
    settings_edits[user_id] = {"chat_id": chat_id, "message_id": message_id,
                               "view": "main", "field": None}
    try:
        settings_edits[user_id]["revision"] = load_profile(
            config.data_dir, user_id, config.calendar_timezone
        )["revision"]
    except ProfileError:
        pass


def _connect_markup(url: str) -> InlineKeyboardMarkup:
    state = parse_qs(urlsplit(url).query)["state"][0]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Connect Google Calendar", url=url)],
        [InlineKeyboardButton(text="Back", callback_data=f"cancel_connect:{state}")],
    ])


def _settings_view(user_id: int, config) -> tuple[str, InlineKeyboardMarkup | None]:
    heading = "⚙️ <b>Settings</b>\n\n<b>Google Calendar</b>\n"
    status = "Not configured"
    account = ""
    if oauth_is_configured(config):
        try:
            token = load_token(config.data_dir, user_id, config.calendar_token_encryption_key)
            if (
                token_is_usable(token)
                and token["connection_generation"] == connection_generation(config.data_dir, user_id)
            ):
                status = "Connected"
                account = f"\nAccount: {escape(token['account_email'])}"
            elif token is not None:
                status = "Reconnect required"
            else:
                status = "Not connected"
        except (CalendarAuthError, KeyError, TypeError):
            status = "Reconnect required"
    try:
        profile = load_profile(config.data_dir, user_id, config.calendar_timezone)
    except ProfileError:
        return heading + f"Status: {status}{account}\n\n⚠️ Profile needs repair. Automatic writes disabled.", (
            _settings_markup(status) if oauth_is_configured(config) else None
        )
    text = (
        heading + f"Status: {status}{account}\n\n<b>Preferences</b>\n"
        f"Automatic writes: {'On ✅' if profile['auto_write_enabled'] else 'Off ❌'}\n"
        f"Timezone: <code>{escape(profile['timezone'])}</code>\n"
        f"Built-in durations: {len(semantic.DEFAULT_DURATION)} types\n"
        f"Custom types: {len(profile['custom_types'])}/20\n"
        "Risky or incomplete events always need review."
    )
    rows = _settings_markup(status).inline_keyboard if oauth_is_configured(config) else []
    rows += [
        [InlineKeyboardButton(text="Automatic writes", callback_data="pref:auto"),
         InlineKeyboardButton(text="Timezone", callback_data="pref:zone")],
        [InlineKeyboardButton(text="Built-in durations", callback_data="pref:builtins"),
         InlineKeyboardButton(text="Custom types", callback_data="pref:customs")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "connect_calendar")
async def connect_calendar_button(
    callback: CallbackQuery, config, settings_messages: dict, settings_edits: dict | None = None
) -> None:
    try:
        url = create_authorization_url(config, callback.from_user.id)
    except CalendarAuthError:
        await callback.answer("Google Calendar connection is not configured.", show_alert=True)
        return
    await callback.answer()
    text = (
        "⚙️ <b>Settings</b>\n\n<b>Google Calendar</b>\n"
        "Use button below to authorize Google. Link expires in 10 minutes."
    )
    markup = _connect_markup(url)
    if callback.message.text and callback.message.text.startswith(("⚙️ Settings", "Google Calendar:")):
        await callback.message.edit_text(text, reply_markup=markup)
        settings_messages[callback.from_user.id] = (
            callback.message.chat.id, callback.message.message_id
        )
    else:
        sent = await callback.message.answer(text, reply_markup=markup)
        settings_messages[callback.from_user.id] = (sent.chat.id, sent.message_id)
    _remember_card(settings_edits, callback.from_user.id, *settings_messages[callback.from_user.id], config)


@router.callback_query(F.data.startswith("cancel_connect:"))
async def cancel_connect(
    callback: CallbackQuery, config, settings_messages: dict, settings_edits: dict | None = None
) -> None:
    state = callback.data.removeprefix("cancel_connect:")
    if not cancel_oauth_state(config.data_dir, state, callback.from_user.id):
        await callback.answer("Cannot cancel this connection.", show_alert=True)
        return
    text, markup = _settings_view(callback.from_user.id, config)
    await callback.message.edit_text(text, reply_markup=markup)
    settings_messages[callback.from_user.id] = (
        callback.message.chat.id, callback.message.message_id
    )
    _remember_card(settings_edits, callback.from_user.id, callback.message.chat.id, callback.message.message_id, config)
    await callback.answer()


def _settings_markup(status: str) -> InlineKeyboardMarkup:
    label = "Switch account" if status == "Connected" else (
        "Reconnect" if status == "Reconnect required" else "Connect"
    )
    rows = [[InlineKeyboardButton(text=label, callback_data="connect_calendar")]]
    if status != "Not connected":
        rows.append([InlineKeyboardButton(text="Disconnect", callback_data="disconnect_calendar")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "disconnect_calendar")
async def disconnect_calendar_button(
    callback: CallbackQuery, config, settings_edits: dict
) -> None:
    state = settings_edits.get(callback.from_user.id)
    if state is None or state.get("view") != "main" or (state["chat_id"], state["message_id"]) != (
        callback.message.chat.id, callback.message.message_id
    ):
        await callback.answer("Open /settings for a fresh card.", show_alert=True)
        return
    state.update(view="disconnect_confirm", field=None)
    await callback.message.edit_text(
        "Disconnect Google Calendar? Pending event confirmations from this account will no longer work. Your existing Google Calendar events remain unchanged.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Disconnect", callback_data="disconnect_calendar_confirm")],
            [InlineKeyboardButton(text="Back", callback_data="disconnect_calendar_back")],
        ]),
    )
    await callback.answer()


def _pending_disconnect(callback: CallbackQuery, settings_edits: dict) -> dict | None:
    state = settings_edits.get(callback.from_user.id)
    if state is None or state.get("view") != "disconnect_confirm" or (
        state["chat_id"], state["message_id"]
    ) != (callback.message.chat.id, callback.message.message_id):
        return None
    return state


@router.callback_query(F.data == "disconnect_calendar_back")
async def cancel_disconnect_calendar_button(
    callback: CallbackQuery, config, settings_edits: dict
) -> None:
    if _pending_disconnect(callback, settings_edits) is None:
        await callback.answer("Open /settings for a fresh card.", show_alert=True)
        return
    text, markup = _settings_view(callback.from_user.id, config)
    await callback.message.edit_text(text, reply_markup=markup)
    _remember_card(settings_edits, callback.from_user.id, callback.message.chat.id, callback.message.message_id, config)
    await callback.answer()


@router.callback_query(F.data == "disconnect_calendar_confirm")
async def confirm_disconnect_calendar_button(
    callback: CallbackQuery, config, settings_edits: dict
) -> None:
    state = _pending_disconnect(callback, settings_edits)
    if state is None:
        await callback.answer("Open /settings for a fresh card.", show_alert=True)
        return
    state["view"] = "main"  # Consume confirmation before awaiting Google revocation.
    removed = await disconnect_and_revoke(config, callback.from_user.id)
    text, markup = _settings_view(callback.from_user.id, config)
    await callback.message.edit_text(text, reply_markup=markup)
    _remember_card(settings_edits, callback.from_user.id, callback.message.chat.id, callback.message.message_id, config)
    await callback.answer("Google Calendar disconnected." if removed else "No connection found.")


@router.message(Command("settings"))
async def settings(
    message: Message, bot: Bot, config, settings_messages: dict,
    settings_edits: dict | None = None, drafts: dict | None = None,
    calendar_edits: dict | None = None,
) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if (drafts and user_id in drafts) or (calendar_edits and user_id in calendar_edits):
        await message.answer("Finish or cancel the active event first, then open /settings.")
        return
    text, markup = _settings_view(user_id, config)
    previous = settings_messages.get(user_id)
    sent = await message.answer(text, reply_markup=markup)
    settings_messages[user_id] = (sent.chat.id, sent.message_id)
    if settings_edits is not None:
        _remember_card(settings_edits, user_id, sent.chat.id, sent.message_id, config)
    if previous and previous != settings_messages[user_id]:
        try:
            await bot.delete_message(chat_id=previous[0], message_id=previous[1])
        except TelegramAPIError:
            pass  # Telegram cannot delete bot messages older than 48 hours.


def _grid(buttons: list[InlineKeyboardButton]) -> list[list[InlineKeyboardButton]]:
    return [buttons[index:index + 2] for index in range(0, len(buttons), 2)]


def _menu(profile: dict, view: str, selected: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    rows = []
    if view == "builtins":
        text = "⚙️ <b>Built-in durations</b>\nTap type to change duration (minutes).\n\n" + "\n".join(
            f"{name.title()}: {profile['type_durations'].get(name, minutes)} min"
            for name, minutes in semantic.DEFAULT_DURATION.items()
        )
        rows = _grid([InlineKeyboardButton(text=name.title(), callback_data=f"pref:built:{name}")
                      for name in semantic.DEFAULT_DURATION])
    elif view == "customs":
        text = "⚙️ <b>Custom types</b>\nTap type to edit its duration or delete it.\n\n" + (
            "\n".join(f"{escape(item['type'])}: {item['duration_minutes']} min"
                      for item in profile["custom_types"]) or "No custom types yet."
        )
        rows = _grid([InlineKeyboardButton(text=item["type"], callback_data=f"pref:item:{item['id']}")
                      for item in profile["custom_types"]])
        if len(profile["custom_types"]) < 20:
            rows.append([InlineKeyboardButton(text="➕ Add type", callback_data="pref:add")])
    elif view == "item":
        item = next((entry for entry in profile["custom_types"] if entry["id"] == selected), None)
        if item is None:
            return _menu(profile, "customs")
        text = f"⚙️ <b>{escape(item['type'])}</b>\nDuration: {item['duration_minutes']} min\nRename: delete and recreate."
        rows = [[InlineKeyboardButton(text="Edit duration", callback_data=f"pref:duration:{selected}"),
                 InlineKeyboardButton(text="Delete", callback_data=f"pref:delete:{selected}")]]
    else:
        raise ValueError("Unknown settings view")
    rows.append([InlineKeyboardButton(text="Back", callback_data="pref:back")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _prompt() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Back", callback_data="pref:back"),
    ]])


async def _show(callback: CallbackQuery, config, state: dict, view: str, selected: str | None = None) -> None:
    profile = load_profile(config.data_dir, callback.from_user.id, config.calendar_timezone)
    if view == "main":
        text, markup = _settings_view(callback.from_user.id, config)
    else:
        text, markup = _menu(profile, view, selected)
    state.update(view=view, field=None, selected=selected, revision=profile["revision"])
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("pref:"))
async def preferences(
    callback: CallbackQuery, config, settings_edits: dict, drafts: dict, calendar_edits: dict,
) -> None:
    user_id = callback.from_user.id
    state = settings_edits.get(user_id)
    if state is None or (state["chat_id"], state["message_id"]) != (
        callback.message.chat.id, callback.message.message_id
    ):
        await callback.answer("Open /settings for a fresh card.", show_alert=True)
        return
    if user_id in drafts or user_id in calendar_edits:
        await callback.answer("Finish or cancel the active event first.", show_alert=True)
        return
    action = callback.data.removeprefix("pref:")
    try:
        profile = load_profile(config.data_dir, user_id, config.calendar_timezone)
        if action == "back":
            previous = state.get("view", "main")
            await _show(callback, config, state, "customs" if previous in {"item", "delete"} or state.get("field") in {"custom_name", "custom_duration", "new_duration"} else
                        "builtins" if state.get("field") == "builtin_duration" else "main")
        elif action in {"builtins", "customs"}:
            await _show(callback, config, state, action)
        elif action.startswith("built:") and action[6:] in semantic.DEFAULT_DURATION:
            name = action[6:]
            state.update(view="builtins", field="builtin_duration", selected=name, revision=profile["revision"],
                         expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
            await callback.message.edit_text(
                f"Duration for <b>{name.title()}</b>: reply with 1–1440 minutes.", reply_markup=_prompt()
            )
        elif action == "zone":
            state.update(view="main", field="timezone", revision=profile["revision"],
                         expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
            await callback.message.edit_text(
                f"Current timezone: <code>{escape(profile['timezone'])}</code>\nReply with exact IANA name (e.g. Europe/Chisinau).",
                reply_markup=_prompt(),
            )
        elif action == "auto":
            if state.get("revision") != profile["revision"]:
                raise ProfileConflict("Settings changed; reload and try again")
            if profile["auto_write_enabled"]:
                profile["auto_write_enabled"] = False
                save_profile(config.data_dir, user_id, config.calendar_timezone, profile)
                await _show(callback, config, state, "main")
            else:
                state.update(view="auto_warning", revision=profile["revision"], field=None)
                await callback.message.edit_text(
                    "⚠️ <b>Enable automatic writes?</b>\nWhen Google writes are enabled, safe, complete events will be created in your connected Google Calendar without confirmation. Risky or incomplete events still require review.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Enable", callback_data="pref:auto_yes")],
                        [InlineKeyboardButton(text="Back", callback_data="pref:back")],
                    ]),
                )
        elif action == "auto_yes" and state["view"] == "auto_warning":
            profile["revision"] = state["revision"]
            profile["auto_write_enabled"] = True
            save_profile(config.data_dir, user_id, config.calendar_timezone, profile)
            await _show(callback, config, state, "main")
        elif action == "add" and len(profile["custom_types"]) < 20:
            state.update(view="customs", field="custom_name", revision=profile["revision"],
                         expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
            await callback.message.edit_text("New custom type: reply with a name (1–40 letters, numbers, spaces or hyphens).",
                                             reply_markup=_prompt())
        elif action.startswith("item:"):
            await _show(callback, config, state, "item", action[5:])
        elif action.startswith("duration:") and any(item["id"] == action[9:] for item in profile["custom_types"]):
            item = next(item for item in profile["custom_types"] if item["id"] == action[9:])
            state.update(view="item", field="custom_duration", selected=item["id"],
                         revision=profile["revision"], expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
            await callback.message.edit_text(f"Duration for <b>{escape(item['type'])}</b>: reply with 1–1440 minutes.",
                                             reply_markup=_prompt())
        elif action.startswith("delete:") and any(item["id"] == action[7:] for item in profile["custom_types"]):
            item = next(item for item in profile["custom_types"] if item["id"] == action[7:])
            state.update(view="delete", selected=item["id"], revision=profile["revision"], field=None)
            await callback.message.edit_text(f"Delete <b>{escape(item['type'])}</b>? Existing events stay unchanged.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Delete type", callback_data=f"pref:delete_yes:{item['id']}")],
                    [InlineKeyboardButton(text="Back", callback_data="pref:back")],
                ]))
        elif action.startswith("delete_yes:") and state["view"] == "delete" and action[11:] == state["selected"]:
            profile["revision"] = state["revision"]
            profile["custom_types"] = [item for item in profile["custom_types"] if item["id"] != state["selected"]]
            save_profile(config.data_dir, user_id, config.calendar_timezone, profile)
            await _show(callback, config, state, "customs")
        else:
            await callback.answer("This settings action expired. Open /settings.", show_alert=True)
            return
    except (ProfileError, ProfileConflict) as error:
        state["field"] = None
        await callback.answer(str(error), show_alert=True)
        text, markup = _settings_view(user_id, config)
        _remember_card(settings_edits, user_id, state["chat_id"], state["message_id"], config)
        await callback.message.edit_text(text, reply_markup=markup)
        return
    await callback.answer()


async def answer_settings(message: Message, bot: Bot, config, settings_edits: dict) -> None:
    user_id = message.from_user.id
    state = settings_edits[user_id]
    if datetime.now(timezone.utc) >= state["expires_at"]:
        state["field"] = None
        await message.answer("Settings input expired. Open /settings to try again.")
        return
    field = state["field"]
    value = message.text.strip()
    try:
        profile = load_profile(config.data_dir, user_id, config.calendar_timezone)
        if profile["revision"] != state["revision"]:
            raise ProfileConflict("Settings changed; reload and try again")
        if field == "timezone":
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise ProfileError("Use an exact IANA timezone, e.g. Europe/Chisinau") from error
            profile["timezone"] = value
        elif field == "custom_name":
            name = clean_label(value)
            if len(profile["custom_types"]) >= 20 or label_key(name) in {
                label_key(item["type"]) for item in profile["custom_types"]
            }:
                raise ProfileError("Type name already exists or 20-type limit reached")
            state.update(field="new_duration", label=name)
            await bot.edit_message_text(
                f"<b>{escape(name)}</b>: reply with duration in minutes (1–1440).",
                chat_id=state["chat_id"], message_id=state["message_id"], reply_markup=_prompt(),
            )
            return
        elif field in {"builtin_duration", "custom_duration", "new_duration"}:
            if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 1440:
                raise ProfileError("Reply with whole minutes from 1 to 1440")
            minutes = int(value)
            if field == "builtin_duration":
                profile["type_durations"][state["selected"]] = minutes
            elif field == "new_duration":
                profile["custom_types"].append(new_custom_type(state["label"], minutes))
            else:
                next(item for item in profile["custom_types"] if item["id"] == state["selected"])["duration_minutes"] = minutes
        else:
            raise ProfileError("Settings input expired")
        save_profile(config.data_dir, user_id, config.calendar_timezone, profile)
    except ProfileConflict as error:
        state["field"] = None
        await message.answer(f"⚠️ {escape(str(error))}")
        text, markup = _settings_view(user_id, config)
        await bot.edit_message_text(text, chat_id=state["chat_id"], message_id=state["message_id"], reply_markup=markup)
        state["view"] = "main"
        state["revision"] = load_profile(config.data_dir, user_id, config.calendar_timezone)["revision"]
        return
    except (ProfileError, StopIteration) as error:
        await message.answer(f"⚠️ {escape(str(error)) if str(error) else 'Type no longer exists.'}")
        return
    state["field"] = None
    view = "builtins" if field == "builtin_duration" else "customs" if field in {"new_duration", "custom_duration"} else "main"
    profile = load_profile(config.data_dir, user_id, config.calendar_timezone)
    text, markup = _menu(profile, view) if view != "main" else _settings_view(user_id, config)
    state["view"] = view
    await bot.edit_message_text(text, chat_id=state["chat_id"], message_id=state["message_id"], reply_markup=markup)


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
    settings_edits = request.app.get("settings_edits")
    bot = request.app.get("bot")
    if messages is not None and bot is not None and pending["telegram_user_id"] in messages:
        chat_id, message_id = messages[pending["telegram_user_id"]]
        text, markup = _settings_view(pending["telegram_user_id"], config)
        try:
            await bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=markup
            )
            _remember_card(settings_edits, pending["telegram_user_id"], chat_id, message_id, config)
        except TelegramAPIError:
            messages.pop(pending["telegram_user_id"], None)
    if bot is not None:
        try:
            await bot.send_message(
                pending["telegram_user_id"],
                f"Google Calendar connected: {escape(account_email)}",
            )
        except TelegramAPIError:
            pass
    return web.Response(text="Google Calendar connected. Return to Telegram.")


def callback_app(
    config, storage, bot: Bot, settings_messages: dict, settings_edits: dict | None = None
) -> web.Application:
    app = web.Application()
    app["config"] = config
    app["storage"] = storage
    app["bot"] = bot
    app["settings_messages"] = settings_messages
    app["settings_edits"] = settings_edits
    app.router.add_get("/google/callback", google_callback)
    return app
