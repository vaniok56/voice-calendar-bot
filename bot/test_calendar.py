import asyncio
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
    consume_oauth_state,
    create_authorization_url,
    create_write,
    disconnect,
    edit_payload,
    insert_event,
    load_write,
    load_token,
    recurrence_to_rrule,
    refresh_access_token,
    reminders_for,
    save_token,
    update_write,
)


EVENT_ID = "a1234"


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
        )

    def test_state_is_single_use_and_tied_to_user(self):
        now = datetime(2026, 9, 22, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            url = create_authorization_url(self.config(directory), 123, now=now)
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
            save_token(root, 123, token)
            path = root / "google-calendar" / "tokens" / "123.json"
            self.assertEqual(load_token(root, 123), token)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertTrue(disconnect(root, 123))
            self.assertIsNone(load_token(root, 123))


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
