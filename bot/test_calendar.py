import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from .calendar import (
    CalendarAuthError,
    CalendarPayloadError,
    build_event,
    consume_oauth_state,
    create_authorization_url,
    disconnect,
    load_token,
    recurrence_to_rrule,
    reminders_for,
    save_token,
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


if __name__ == "__main__":
    unittest.main()
