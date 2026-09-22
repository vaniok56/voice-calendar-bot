import unittest

from .calendar import CalendarPayloadError, build_event, recurrence_to_rrule, reminders_for


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


if __name__ == "__main__":
    unittest.main()
