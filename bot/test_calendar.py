import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest

from .calendar import (
    CalendarAuthError,
    CalendarPayloadError,
    build_event,
    claim_write,
    consume_oauth_state,
    connection_generation,
    create_authorization_url,
    create_write,
    cancel_oauth_state,
    disconnect,
    disconnect_and_revoke,
    edit_payload,
    exchange_code,
    fetch_account_email,
    insert_event,
    load_write,
    load_token,
    pending_connection,
    recurrence_to_rrule,
    refresh_access_token,
    reminders_for,
    save_token,
    token_is_usable,
    update_write,
)
from .handlers.calendar import (
    _settings_view,
    answer_settings,
    cancel_connect,
    cancel_disconnect_calendar_button,
    confirm_disconnect_calendar_button,
    connect_calendar_button,
    disconnect_calendar_button,
    google_callback,
    settings,
    preferences,
)
from .profile import ProfileConflict, ProfileError, load_profile, save_profile


EVENT_ID = "a1234"
TOKEN_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


class TestProfiles(unittest.TestCase):
    def test_private_profile_conflict_and_invalid_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = load_profile(root, 42, "Asia/Tokyo")
            self.assertEqual(profile["timezone"], "Asia/Tokyo")
            self.assertFalse(profile["auto_write_enabled"])
            path = root / "google-calendar" / "profiles" / "42.json"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            profile["auto_write_enabled"] = True
            saved = save_profile(root, 42, "Asia/Tokyo", profile)
            self.assertEqual(saved["revision"], 2)
            with self.assertRaises(ProfileConflict):
                save_profile(root, 42, "Asia/Tokyo", profile)
            path.write_text('{"version": 99}')
            with self.assertRaises(ProfileError):
                load_profile(root, 42, "Asia/Tokyo")

    def test_settings_custom_type_wizard_and_builtin_grid(self):
        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(data_dir=Path(directory), calendar_timezone="Europe/Chisinau",
                                     google_oauth_client_id=None, google_oauth_client_secret=None,
                                     google_oauth_redirect_uri=None)
            message = MagicMock()
            message.from_user.id = 42
            message.answer = AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=42), message_id=5))
            bot = SimpleNamespace(delete_message=AsyncMock(), edit_message_text=AsyncMock())
            state = {}
            asyncio.run(settings(message, bot, config, {}, settings_edits=state, drafts={}, calendar_edits={}))
            callback = MagicMock()
            callback.from_user.id = 42
            callback.message.chat.id = 42
            callback.message.message_id = 5
            callback.message.edit_text = AsyncMock()
            callback.answer = AsyncMock()
            for action in ("pref:builtins", "pref:customs", "pref:add"):
                callback.data = action
                asyncio.run(preferences(callback, config, state, {}, {}))
            builtins_markup = callback.message.edit_text.await_args_list[0].kwargs["reply_markup"]
            self.assertEqual(len(builtins_markup.inline_keyboard[0]), 2)
            self.assertEqual(state[42]["field"], "custom_name")
            self.assertEqual([button.text for row in callback.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
                              for button in row], ["Back"])
            message.text = "Gym"
            asyncio.run(answer_settings(message, bot, config, state))
            message.text = "90"
            asyncio.run(answer_settings(message, bot, config, state))
            profile = load_profile(config.data_dir, 42, config.calendar_timezone)
            self.assertEqual(profile["custom_types"][0]["type"], "Gym")
            self.assertEqual(profile["custom_types"][0]["duration_minutes"], 90)
            self.assertEqual(state[42]["view"], "customs")

    def test_settings_status_is_per_user_and_prompts_have_one_back(self):
        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(data_dir=Path(directory), calendar_timezone="Europe/Chisinau",
                                     google_oauth_client_id=None, google_oauth_client_secret=None,
                                     google_oauth_redirect_uri=None, calendar_write_enabled=True)
            text, _ = _settings_view(42, config)
            self.assertIn("Automatic writes: Off ❌", text)
            self.assertNotIn("Google writes:", text)
            profile = load_profile(config.data_dir, 42, config.calendar_timezone)
            profile["auto_write_enabled"] = True
            save_profile(config.data_dir, 42, config.calendar_timezone, profile)
            self.assertIn("Automatic writes: On ✅", _settings_view(42, config)[0])
            callback = MagicMock()
            callback.from_user.id = 42
            callback.message.chat.id = 42
            callback.message.message_id = 5
            callback.message.edit_text = AsyncMock()
            callback.answer = AsyncMock()
            edits = {42: {"chat_id": 42, "message_id": 5, "view": "main", "field": None,
                          "revision": 2}}
            for action in ("pref:zone", "pref:back", "pref:builtins", "pref:built:meeting"):
                callback.data = action
                asyncio.run(preferences(callback, config, edits, {}, {}))
                if action in {"pref:zone", "pref:built:meeting"}:
                    markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
                    self.assertEqual([button.text for row in markup.inline_keyboard for button in row], ["Back"])


class TestCalendarPayload(unittest.TestCase):
    def timed(self, **updates):
        event = {
            "title": "Sync",
            "start": "2026-09-19T09:00:00+03:00",
            "date": "2026-09-19",
            "all_day": False,
            "duration_minutes": 60,
            "location": None,
            "recurrence": None,
            "reminders_minutes": [],
        }
        event.update(updates)
        return event

    def test_timed_event_uses_default_reminders(self):
        event = build_event(self.timed(), EVENT_ID)
        self.assertEqual(event["start"]["dateTime"], "2026-09-19T09:00:00+03:00")
        self.assertEqual(event["end"]["dateTime"], "2026-09-19T10:00:00+03:00")
        self.assertEqual(event["reminders"], {"useDefault": True})

    def test_timed_duration_crosses_dst_as_elapsed_time(self):
        event = build_event(self.timed(
            start="2026-03-29T00:30:00+02:00", duration_minutes=180
        ), EVENT_ID)
        self.assertEqual(event["end"]["dateTime"], "2026-03-29T04:30:00+03:00")

    def test_timed_event_uses_configured_timezone(self):
        event = build_event(self.timed(), EVENT_ID, "America/New_York")
        self.assertEqual(event["start"]["dateTime"], "2026-09-19T02:00:00-04:00")
        self.assertEqual(event["start"]["timeZone"], "America/New_York")

    def test_all_day_event_has_exclusive_end(self):
        event = build_event(self.timed(
            title="Daniel birthday", all_day=True, date="2027-02-03", start=None
        ), EVENT_ID)
        self.assertEqual(event["start"], {"date": "2027-02-03"})
        self.assertEqual(event["end"], {"date": "2027-02-04"})

    def test_explicit_reminders_become_popup_overrides(self):
        event = build_event(self.timed(reminders_minutes=[60, 5]), EVENT_ID)
        self.assertEqual(event["reminders"]["overrides"], [
            {"method": "popup", "minutes": 60},
            {"method": "popup", "minutes": 5},
        ])

    def test_rejects_invalid_reminders(self):
        with self.assertRaises(CalendarPayloadError):
            reminders_for([1, 2, 3, 4, 5, 6])
        with self.assertRaises(CalendarPayloadError):
            reminders_for([40321])

    def test_recurrence_rrules(self):
        self.assertEqual(recurrence_to_rrule({"freq": "weekly", "weekdays": [0]}), [
            "RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO"
        ])
        self.assertEqual(recurrence_to_rrule({
            "freq": "monthly", "weekdays": [4], "position": 3,
        }), ["RRULE:FREQ=MONTHLY;INTERVAL=1;BYDAY=FR;BYSETPOS=3"])
        self.assertEqual(recurrence_to_rrule({
            "freq": "yearly", "month": 3, "month_day": 8,
        }), ["RRULE:FREQ=YEARLY;INTERVAL=1;BYMONTH=3;BYMONTHDAY=8"])
        self.assertEqual(recurrence_to_rrule(
            {"freq": "weekly", "until": "2026-10-01"},
            timed=True,
            start=datetime.fromisoformat("2026-09-19T09:00:00+03:00"),
        ), ["RRULE:FREQ=WEEKLY;INTERVAL=1;UNTIL=20261001T205959Z"])
        with self.assertRaises(CalendarPayloadError):
            recurrence_to_rrule({"freq": "weekly", "count": 2, "until": "2026-10-01"})

    def test_rejects_invalid_event_id(self):
        with self.assertRaises(CalendarPayloadError):
            build_event(self.timed(), "invalid-id")


class TestCalendarOAuthStorage(unittest.TestCase):
    def config(self, data_dir):
        return SimpleNamespace(
            data_dir=Path(data_dir),
            google_oauth_client_id="client-id",
            google_oauth_client_secret="client-secret",
            google_oauth_redirect_uri="https://calendar.example.com/google/callback",
            calendar_timezone="Europe/Chisinau",
            calendar_token_encryption_key=TOKEN_KEY,
        )

    def test_connect_and_back_edit_card_and_invalidate_state(self):
        callback = MagicMock()
        callback.from_user.id = 123
        callback.answer = AsyncMock()
        callback.message.text = "⚙️ Settings\n\nGoogle Calendar\nStatus: Not connected"
        callback.message.chat.id = 123
        callback.message.message_id = 456
        callback.message.edit_text = AsyncMock()
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            messages = {}
            asyncio.run(connect_calendar_button(callback, config, messages))
            text = callback.message.edit_text.await_args.args[0]
            markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
            button = markup.inline_keyboard[0][0]
            self.assertNotIn("https://", text)
            self.assertTrue(button.url.startswith("https://accounts.google.com/"))
            self.assertIsNone(button.callback_data)
            self.assertEqual(markup.inline_keyboard[1][0].text, "Back")
            cancel_data = markup.inline_keyboard[1][0].callback_data
            self.assertLessEqual(len(cancel_data.encode()), 64)
            state = cancel_data.removeprefix("cancel_connect:")
            self.assertEqual(parse_qs(urlparse(button.url).query)["state"], [state])
            callback.data = cancel_data
            callback.from_user.id = 999
            asyncio.run(cancel_connect(callback, config, messages))
            self.assertEqual(callback.message.edit_text.await_count, 1)
            callback.from_user.id = 123
            asyncio.run(cancel_connect(callback, config, messages))
            self.assertEqual(callback.message.edit_text.await_count, 2)
            self.assertIn("Not connected", callback.message.edit_text.await_args.args[0])
            self.assertEqual(messages[123], (123, 456))
            self.assertFalse(pending_connection(config.data_dir, 123, state))
            with self.assertRaises(CalendarAuthError):
                consume_oauth_state(config.data_dir, state)

    def test_connect_from_event_card_creates_separate_settings_card(self):
        callback = MagicMock()
        callback.from_user.id = 123
        callback.message.text = "Calendar event"
        callback.answer = AsyncMock()
        callback.message.answer = AsyncMock()
        callback.message.edit_text = AsyncMock()
        with tempfile.TemporaryDirectory() as directory:
            messages = {}
            asyncio.run(connect_calendar_button(callback, self.config(directory), messages))
            callback.message.edit_text.assert_not_awaited()
            callback.message.answer.assert_awaited_once()
            self.assertIn(123, messages)

    def test_cancel_switch_keeps_existing_connection_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            save_token(config.data_dir, 123, {"connection_generation": 0}, TOKEN_KEY)
            state = parse_qs(urlparse(create_authorization_url(config, 123)).query)["state"][0]
            self.assertEqual(connection_generation(config.data_dir, 123), 0)
            self.assertTrue(pending_connection(config.data_dir, 123, state))
            self.assertTrue(cancel_oauth_state(config.data_dir, state, 123))
            self.assertEqual(connection_generation(config.data_dir, 123), 0)
            self.assertFalse(pending_connection(config.data_dir, 123, state))
            self.assertEqual(load_token(config.data_dir, 123, TOKEN_KEY)["connection_generation"], 0)

    def test_state_is_single_use_and_tied_to_user(self):
        now = datetime(2026, 9, 22, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            url = create_authorization_url(self.config(directory), 123, now=now)
            scopes = set(parse_qs(urlparse(url).query)["scope"][0].split())
            self.assertIn("openid", scopes)
            self.assertIn("https://www.googleapis.com/auth/userinfo.email", scopes)
            state = parse_qs(urlparse(url).query)["state"][0]
            pending = consume_oauth_state(Path(directory), state, now=now)
            self.assertEqual(pending["telegram_user_id"], 123)
            with self.assertRaises(CalendarAuthError):
                consume_oauth_state(Path(directory), state, now=now)

    def test_expired_or_unknown_state_fails(self):
        now = datetime(2026, 9, 22, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            state = parse_qs(urlparse(create_authorization_url(config, 123, now=now)).query)["state"][0]
            with self.assertRaises(CalendarAuthError):
                consume_oauth_state(Path(directory), state, now=now + timedelta(minutes=10))
            with self.assertRaises(CalendarAuthError):
                consume_oauth_state(Path(directory), "x" * 43, now=now)

    def test_token_is_private_and_disconnects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = {"access_token": "access", "refresh_token": "refresh"}
            save_token(root, 123, token, TOKEN_KEY)
            path = root / "google-calendar" / "tokens" / "123.json"
            self.assertEqual(load_token(root, 123, TOKEN_KEY), token)
            self.assertNotIn("refresh", path.read_text(encoding="utf-8"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertTrue(disconnect(root, 123))
            self.assertIsNone(load_token(root, 123, TOKEN_KEY))

    def test_plaintext_token_migrates_on_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "google-calendar" / "tokens" / "123.json"
            path.parent.mkdir(parents=True)
            path.write_text('{"refresh_token":"legacy"}', encoding="utf-8")
            self.assertEqual(load_token(root, 123, TOKEN_KEY), {"refresh_token": "legacy"})
            self.assertNotIn("legacy", path.read_text(encoding="utf-8"))

    def test_rejects_invalid_user_id_for_connection_path(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CalendarAuthError):
                connection_generation(Path(directory), "../token")

    def test_disconnect_invalidates_pending_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            url = create_authorization_url(config, 123)
            state = parse_qs(urlparse(url).query)["state"][0]
            pending = consume_oauth_state(Path(directory), state)
            disconnect(Path(directory), 123)
            self.assertNotEqual(
                pending["connection_generation"] - 1, connection_generation(Path(directory), 123)
            )
            self.assertFalse(pending_connection(Path(directory), 123, state))

    def test_legacy_token_requires_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_token(root, 123, {"access_token": "old", "scope": ["https://www.googleapis.com/auth/calendar.events.owned"]}, TOKEN_KEY)
            self.assertFalse(token_is_usable(load_token(root, 123, TOKEN_KEY)))
            token = {
                "schema_version": 2, "connection_generation": 0,
                "access_token": "access", "account_email": "user@example.com", "refresh_token": "refresh",
                "scope": ["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/calendar.events.owned"],
            }
            self.assertTrue(token_is_usable(token))
            self.assertFalse(token_is_usable({**token, "scope": ["openid"]}))

    @patch("bot.calendar.aiohttp.ClientSession")
    def test_identity_requires_verified_email_and_successful_lookup(self, client_session):
        for status, body in ((200, {"email": "a@example.com", "email_verified": False}),
                             (200, {"email_verified": True}), (503, {})):
            with self.subTest(body=body):
                client_session.return_value = TestCalendarWrites().session(
                    MagicMock(),
                    TestCalendarWrites().response(status, body),
                )
                with self.assertRaises(CalendarAuthError):
                    asyncio.run(fetch_account_email("access"))
        client_session.return_value = TestCalendarWrites().session(
            MagicMock(),
            TestCalendarWrites().response(200, {"email": "a@example.com", "email_verified": True}),
        )
        self.assertEqual(asyncio.run(fetch_account_email("access")), "a@example.com")

    @patch("bot.calendar.aiohttp.ClientSession")
    def test_code_exchange_requires_all_scopes(self, client_session):
        client_session.return_value = TestCalendarWrites().session(
            TestCalendarWrites().response(200, {
                "access_token": "access", "refresh_token": "refresh", "expires_in": 3600,
                "scope": "https://www.googleapis.com/auth/calendar.events.owned",
            })
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CalendarAuthError):
                asyncio.run(exchange_code(self.config(directory), "code", "verifier"))

    @patch("bot.calendar.aiohttp.ClientSession")
    def test_disconnect_deletes_credentials_even_if_revocation_fails(self, client_session):
        client_session.return_value = TestCalendarWrites().session(
            TestCalendarWrites().response(503, {})
        )
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            config.calendar_token_encryption_key = TOKEN_KEY
            save_token(config.data_dir, 123, {"refresh_token": "refresh"}, TOKEN_KEY)
            self.assertTrue(asyncio.run(disconnect_and_revoke(config, 123)))
            self.assertIsNone(load_token(config.data_dir, 123, TOKEN_KEY))
            self.assertEqual(connection_generation(config.data_dir, 123), 1)
            self.assertEqual(client_session.return_value.post.call_args.kwargs["data"], {"token": "refresh"})

    @patch("bot.handlers.calendar.fetch_account_email", new_callable=AsyncMock, return_value="user@example.com")
    @patch("bot.handlers.calendar.exchange_code", new_callable=AsyncMock)
    def test_stale_callback_after_disconnect_or_reconnect_never_saves_token(self, exchange, _email):
        exchange.return_value = {"access_token": "access", "refresh_token": "refresh", "scope": []}
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            config.calendar_token_encryption_key = TOKEN_KEY
            storage = SimpleNamespace(is_allowed=lambda user_id: True)
            for action in ("disconnect", "reconnect"):
                url = create_authorization_url(config, 123)
                state = parse_qs(urlparse(url).query)["state"][0]
                if action == "disconnect":
                    disconnect(config.data_dir, 123)
                else:
                    create_authorization_url(config, 123)
                request = SimpleNamespace(
                    app={"config": config, "storage": storage}, query={"state": state, "code": "code"}
                )
                response = asyncio.run(google_callback(request))
                self.assertEqual(response.status, 400)
                self.assertIsNone(load_token(config.data_dir, 123, TOKEN_KEY))

    @patch("bot.handlers.calendar.fetch_account_email", new_callable=AsyncMock, return_value="user@example.com")
    @patch("bot.handlers.calendar.exchange_code", new_callable=AsyncMock)
    def test_callback_saves_verified_identity_and_generation(self, exchange, _email):
        exchange.return_value = {
            "access_token": "access", "refresh_token": "refresh",
            "scope": ["openid", "https://www.googleapis.com/auth/userinfo.email",
                      "https://www.googleapis.com/auth/calendar.events.owned"],
        }
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            config.calendar_token_encryption_key = TOKEN_KEY
            url = create_authorization_url(config, 123)
            state = parse_qs(urlparse(url).query)["state"][0]
            request = SimpleNamespace(
                app={
                    "config": config, "storage": SimpleNamespace(is_allowed=lambda user_id: True),
                    "bot": SimpleNamespace(edit_message_text=AsyncMock(), send_message=AsyncMock()),
                    "settings_messages": {123: (123, 456)},
                },
                query={"state": state, "code": "code"},
            )
            self.assertEqual(asyncio.run(google_callback(request)).status, 200)
            request.app["bot"].edit_message_text.assert_awaited_once()
            self.assertIn("Status: Connected", request.app["bot"].edit_message_text.await_args.args[0])
            self.assertIn("Account: user@example.com", request.app["bot"].edit_message_text.await_args.args[0])
            request.app["bot"].send_message.assert_awaited_once_with(
                123, "Google Calendar connected: user@example.com"
            )
            token = load_token(config.data_dir, 123, TOKEN_KEY)
            self.assertTrue(token_is_usable(token))
            path = config.data_dir / "google-calendar" / "tokens" / "123.json"
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 2)
            self.assertEqual(token["account_email"], "user@example.com")
            self.assertEqual(token["connection_generation"], connection_generation(config.data_dir, 123))

    @patch("bot.handlers.calendar.fetch_account_email", new_callable=AsyncMock)
    @patch("bot.handlers.calendar.exchange_code", new_callable=AsyncMock)
    def test_cancel_during_callback_does_not_replace_existing_account(self, exchange, email):
        exchange.return_value = {"access_token": "new", "refresh_token": "new"}
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            config.calendar_token_encryption_key = TOKEN_KEY
            save_token(config.data_dir, 123, {"access_token": "old", "connection_generation": 0}, TOKEN_KEY)
            state = parse_qs(urlparse(create_authorization_url(config, 123)).query)["state"][0]

            async def cancel_during_lookup(_access_token):
                self.assertTrue(cancel_oauth_state(config.data_dir, state, 123))
                return "new@example.com"

            email.side_effect = cancel_during_lookup
            request = SimpleNamespace(
                app={"config": config, "storage": SimpleNamespace(is_allowed=lambda user_id: True)},
                query={"state": state, "code": "code"},
            )
            self.assertEqual(asyncio.run(google_callback(request)).status, 400)
            self.assertEqual(load_token(config.data_dir, 123, TOKEN_KEY)["access_token"], "old")

    @patch("bot.handlers.calendar.fetch_account_email", new_callable=AsyncMock)
    @patch("bot.handlers.calendar.exchange_code", new_callable=AsyncMock)
    def test_new_link_invalidates_callback_already_in_progress(self, exchange, email):
        exchange.return_value = {"access_token": "new", "refresh_token": "new"}
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            config.calendar_token_encryption_key = TOKEN_KEY
            state = parse_qs(urlparse(create_authorization_url(config, 123)).query)["state"][0]
            replacement = None

            async def replace_during_lookup(_access_token):
                nonlocal replacement
                replacement = parse_qs(urlparse(create_authorization_url(config, 123)).query)["state"][0]
                return "new@example.com"

            email.side_effect = replace_during_lookup
            request = SimpleNamespace(
                app={"config": config, "storage": SimpleNamespace(is_allowed=lambda user_id: True)},
                query={"state": state, "code": "code"},
            )
            self.assertEqual(asyncio.run(google_callback(request)).status, 400)
            self.assertTrue(pending_connection(config.data_dir, 123, replacement))
            self.assertIsNone(load_token(config.data_dir, 123, TOKEN_KEY))

    def test_settings_reports_connection_status_and_timezone(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            config.calendar_token_encryption_key = TOKEN_KEY
            config.calendar_timezone = "Europe/Chisinau"
            message = MagicMock()
            message.from_user.id = 123
            message.answer = AsyncMock()
            message.answer.side_effect = [
                SimpleNamespace(chat=SimpleNamespace(id=123), message_id=number)
                for number in (456, 457, 458)
            ]
            bot = SimpleNamespace(delete_message=AsyncMock())
            messages = {}
            asyncio.run(settings(message, bot, config, messages))
            self.assertIn("Not connected", message.answer.await_args.args[0])
            self.assertEqual(message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].text, "Connect")
            save_token(config.data_dir, 123, {"access_token": "legacy"}, TOKEN_KEY)
            asyncio.run(settings(message, bot, config, messages))
            self.assertIn("Reconnect required", message.answer.await_args.args[0])
            self.assertEqual(message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].text, "Reconnect")
            save_token(config.data_dir, 123, {
                "schema_version": 2, "account_email": "user@example.com", "refresh_token": "refresh",
                "connection_generation": 0, "access_token": "access",
                "scope": ["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/calendar.events.owned"],
            }, TOKEN_KEY)
            asyncio.run(settings(message, bot, config, messages))
            self.assertIn("Status: Connected", message.answer.await_args.args[0])
            self.assertIn("Account: user@example.com", message.answer.await_args.args[0])
            self.assertIn("Europe/Chisinau", message.answer.await_args.args[0])
            buttons = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
            self.assertEqual(buttons[0][0].text, "Switch account")
            self.assertIn("disconnect_calendar", [button.callback_data for row in buttons for button in row])
            self.assertEqual(message.answer.await_count, 3)
            self.assertEqual(bot.delete_message.await_count, 2)
            self.assertEqual(messages[123], (123, 458))

    def test_settings_still_appears_if_old_card_cannot_be_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            message = MagicMock()
            message.from_user.id = 123
            message.answer = AsyncMock()
            message.answer.return_value = SimpleNamespace(chat=SimpleNamespace(id=123), message_id=457)
            bot = SimpleNamespace(delete_message=AsyncMock())
            bot.delete_message.side_effect = TelegramBadRequest(
                method=MagicMock(), message="message to edit not found"
            )
            messages = {123: (123, 456)}
            asyncio.run(settings(message, bot, self.config(directory), messages))
            message.answer.assert_awaited_once()
            self.assertEqual(messages[123], (123, 457))

    @patch("bot.calendar.aiohttp.ClientSession")
    def test_disconnect_requires_confirmation_and_back_preserves_token(self, client_session):
        client_session.return_value = TestCalendarWrites().session(TestCalendarWrites().response(200, {}))
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            config.calendar_token_encryption_key = TOKEN_KEY
            save_token(config.data_dir, 123, {"refresh_token": "refresh"}, TOKEN_KEY)
            callback = MagicMock()
            callback.from_user.id = 123
            callback.message.chat.id = 123
            callback.message.message_id = 456
            callback.answer = AsyncMock()
            callback.message.edit_text = AsyncMock()
            edits = {123: {"chat_id": 123, "message_id": 456, "view": "main", "field": None}}
            asyncio.run(disconnect_calendar_button(callback, config, edits))
            self.assertIsNotNone(load_token(config.data_dir, 123, TOKEN_KEY))
            self.assertIn("Disconnect Google Calendar?", callback.message.edit_text.await_args.args[0])
            self.assertEqual(edits[123]["view"], "disconnect_confirm")
            asyncio.run(cancel_disconnect_calendar_button(callback, config, edits))
            self.assertIsNotNone(load_token(config.data_dir, 123, TOKEN_KEY))
            asyncio.run(confirm_disconnect_calendar_button(callback, config, edits))
            self.assertIsNotNone(load_token(config.data_dir, 123, TOKEN_KEY))
            asyncio.run(disconnect_calendar_button(callback, config, edits))
            asyncio.run(confirm_disconnect_calendar_button(callback, config, edits))
            self.assertIsNone(load_token(config.data_dir, 123, TOKEN_KEY))
            text = callback.message.edit_text.await_args.args[0]
            self.assertIn("Not connected", text)
            buttons = callback.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
            self.assertEqual(buttons[0][0].text, "Connect")
            self.assertEqual(edits[123]["view"], "main")
            asyncio.run(confirm_disconnect_calendar_button(callback, config, edits))
            self.assertEqual(connection_generation(config.data_dir, 123), 1)


class TestCalendarWrites(unittest.TestCase):
    def config(self):
        return SimpleNamespace(
            google_oauth_client_id="client-id",
            google_oauth_client_secret="client-secret",
        )

    def response(self, status, body):
        response = MagicMock()
        response.status = status
        response.json = AsyncMock(return_value=body)
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=None)
        return response

    def session(self, post, get=None):
        session = MagicMock()
        session.post.return_value = post
        if get is not None:
            session.get.return_value = get
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        return session

    def test_write_persists_stable_event_id_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = build_event({
                "title": "Sync", "start": "2026-09-19T09:00:00+03:00",
                "all_day": False, "duration_minutes": 60, "location": None,
                "recurrence": None, "reminders_minutes": [],
            }, "evt0123456789abcdefghijklmnopqrstuv")
            record = create_write(Path(directory), 123, "text/123/456/record.json", payload)
            self.assertEqual(record["status"], "pending")
            self.assertEqual(record["event_id"], payload["id"])
            self.assertLessEqual(len(f"edit_field:{record['write_id']}:title".encode()), 64)
            self.assertEqual(load_write(Path(directory), record["write_id"]), record)
            updated = update_write(
                Path(directory), record["write_id"], status="creating"
            )
            self.assertEqual(updated["event_id"], record["event_id"])
            self.assertEqual(updated["status"], "creating")

    def test_pending_payload_edit_keeps_event_id(self):
        payload = build_event({
            "title": "Sync", "start": "2026-09-19T09:00:00+03:00",
            "all_day": False, "duration_minutes": 60, "location": None,
            "recurrence": None, "reminders_minutes": [],
        }, "evt0123456789abcdefghijklmnopqrstuv")
        edited = edit_payload(payload, "time", "10:30", "Europe/Chisinau")
        self.assertEqual(edited["id"], payload["id"])
        self.assertEqual(edited["start"]["dateTime"], "2026-09-19T10:30:00+03:00")

    def test_creating_write_requires_expired_lease_and_no_active_worker(self):
        from .calendar import ACTIVE_WRITES, release_write

        now = datetime(2026, 9, 22, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = create_write(root, 123, "text/123/456/record.json", {"id": EVENT_ID})
            write_id = record["write_id"]
            self.assertIsNotNone(claim_write(root, write_id, now=now))
            self.assertEqual(load_write(root, write_id)["claimed_at"], now.isoformat())
            self.assertIsNone(claim_write(root, write_id, now=now + timedelta(minutes=11)))
            self.assertIn(write_id, ACTIVE_WRITES)
            release_write(write_id)
            self.assertIsNone(claim_write(root, write_id, now=now + timedelta(minutes=9)))
            self.assertIsNotNone(claim_write(root, write_id, now=now + timedelta(minutes=10)))
            release_write(write_id)
            update_write(root, write_id, claimed_at=None)  # Pre-lease records are stale.
            self.assertIsNotNone(claim_write(root, write_id, now=now + timedelta(minutes=11)))
            release_write(write_id)

    @patch("bot.calendar.aiohttp.ClientSession")
    def test_refresh_access_token(self, client_session):
        session = self.session(self.response(200, {
            "access_token": "new-access", "expires_in": 3600,
        }))
        client_session.return_value = session
        token = asyncio.run(refresh_access_token(self.config(), {
            "access_token": "old-access", "refresh_token": "refresh",
            "expires_at": "2026-09-22T00:00:00+00:00",
        }))
        self.assertEqual(token["access_token"], "new-access")
        self.assertEqual(token["refresh_token"], "refresh")

    @patch("bot.calendar.aiohttp.ClientSession")
    def test_conflict_retry_uses_matching_existing_event(self, client_session):
        payload = build_event({
            "title": "Sync", "start": "2026-09-19T09:00:00+03:00",
            "all_day": False, "duration_minutes": 60, "location": None,
            "recurrence": None, "reminders_minutes": [],
        }, EVENT_ID)
        existing = {**payload, "htmlLink": "https://calendar.google/event"}
        client_session.return_value = self.session(
            self.response(409, {}), self.response(200, existing)
        )
        result = asyncio.run(insert_event({"access_token": "access"}, payload))
        self.assertEqual(result["htmlLink"], "https://calendar.google/event")


if __name__ == "__main__":
    unittest.main()
