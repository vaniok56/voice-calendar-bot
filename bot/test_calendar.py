import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, MagicMock, patch

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
    cancel_connect,
    connect_calendar_button,
    disconnect_calendar_button,
    google_callback,
    settings,
)


EVENT_ID = "a1234"
TOKEN_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


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
        )

    def test_connect_and_cancel_delete_link_and_invalidate_state(self):
        callback = MagicMock()
        callback.from_user.id = 123
        callback.answer = AsyncMock()
        callback.message.answer = AsyncMock()
        callback.message.delete = AsyncMock()
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            asyncio.run(connect_calendar_button(callback, config))
            text = callback.message.answer.await_args.args[0]
            markup = callback.message.answer.await_args.kwargs["reply_markup"]
            button = markup.inline_keyboard[0][0]
            self.assertNotIn("https://", text)
            self.assertTrue(button.url.startswith("https://accounts.google.com/"))
            self.assertIsNone(button.callback_data)
            cancel_data = markup.inline_keyboard[1][0].callback_data
            self.assertLessEqual(len(cancel_data.encode()), 64)
            state = cancel_data.removeprefix("cancel_connect:")
            self.assertEqual(parse_qs(urlparse(button.url).query)["state"], [state])
            callback.data = cancel_data
            callback.from_user.id = 999
            asyncio.run(cancel_connect(callback, config))
            callback.message.delete.assert_not_awaited()
            callback.from_user.id = 123
            asyncio.run(cancel_connect(callback, config))
            callback.message.delete.assert_awaited_once()
            self.assertFalse(pending_connection(config.data_dir, 123, state))
            with self.assertRaises(CalendarAuthError):
                consume_oauth_state(config.data_dir, state)

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
                app={"config": config, "storage": SimpleNamespace(is_allowed=lambda user_id: True)},
                query={"state": state, "code": "code"},
            )
            self.assertEqual(asyncio.run(google_callback(request)).status, 200)
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
            asyncio.run(settings(message, config))
            self.assertIn("Not connected", message.answer.await_args.args[0])
            self.assertEqual(message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].text, "Connect")
            save_token(config.data_dir, 123, {"access_token": "legacy"}, TOKEN_KEY)
            asyncio.run(settings(message, config))
            self.assertIn("Reconnect required", message.answer.await_args.args[0])
            self.assertEqual(message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].text, "Reconnect")
            save_token(config.data_dir, 123, {
                "schema_version": 2, "account_email": "user@example.com", "refresh_token": "refresh",
                "connection_generation": 0, "access_token": "access",
                "scope": ["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/calendar.events.owned"],
            }, TOKEN_KEY)
            asyncio.run(settings(message, config))
            self.assertIn("Connected: user@example.com", message.answer.await_args.args[0])
            self.assertIn("Europe/Chisinau", message.answer.await_args.args[0])
            buttons = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
            self.assertEqual(buttons[0][0].text, "Switch account")
            self.assertIn("disconnect_calendar", [button.callback_data for row in buttons for button in row])

    @patch("bot.calendar.aiohttp.ClientSession")
    def test_disconnect_button_removes_token(self, client_session):
        client_session.return_value = TestCalendarWrites().session(TestCalendarWrites().response(200, {}))
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            config.calendar_token_encryption_key = TOKEN_KEY
            save_token(config.data_dir, 123, {"refresh_token": "refresh"}, TOKEN_KEY)
            callback = MagicMock()
            callback.from_user.id = 123
            callback.answer = AsyncMock()
            callback.message.edit_text = AsyncMock()
            asyncio.run(disconnect_calendar_button(callback, config))
            self.assertIsNone(load_token(config.data_dir, 123, TOKEN_KEY))
            text = callback.message.edit_text.await_args.args[0]
            self.assertIn("Not connected", text)
            buttons = callback.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
            self.assertEqual(buttons[0][0].text, "Connect")


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
