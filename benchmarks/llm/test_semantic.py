import unittest
from datetime import date, datetime

from bot import semantic
from benchmarks.llm import semantic_benchmark


def empty(**updates):
    none_date = {
        "kind": "none", "source": None, "year": None, "month": None, "day": None,
        "weekday": None, "offset_years": 0, "offset_months": 0,
        "offset_weeks": 0, "offset_days": 0,
    }
    none_time = {
        "kind": "none", "source": None, "hour": None, "minute": None,
        "meridiem": "none", "offset_hours": 0, "offset_minutes": 0,
        "approximate": False,
    }
    value = {
        "operation": "create", "event_type": "meeting", "title": "sync",
        "date": none_date, "time": none_time, "all_day": False,
        "duration_minutes": None, "duration_source": None,
        "end_time": dict(none_time), "location": None, "recurrence": None,
        "reminders": [], "corrected": False,
    }
    value.update(updates)
    return value


class TestSemanticResolver(unittest.TestCase):
    REFERENCE = datetime(2026, 9, 18, 12, 0, tzinfo=semantic.ZONE)

    def test_weekday_and_bare_hour(self):
        raw = empty(
            date={
                "kind": "weekday", "source": "Friday", "year": None,
                "month": None, "day": None, "weekday": 4, "offset_years": 0,
                "offset_months": 0, "offset_weeks": 0, "offset_days": 0,
            },
            time={
                "kind": "clock", "source": "4:15", "hour": 4, "minute": 15,
                "meridiem": "unspecified", "offset_hours": 0,
                "offset_minutes": 0, "approximate": False,
            },
        )
        result = semantic.resolve(raw, "sync Friday 4:15", self.REFERENCE)
        self.assertEqual(result["start"], "2026-09-25T04:15:00+03:00")
        self.assertEqual(result["duration_minutes"], 60)
        self.assertNotIn("weekday", result["risks"])
        self.assertTrue(result["auto_write"])

    def test_bare_twelve_means_noon(self):
        raw = empty()
        raw["date"].update(kind="relative", source="tomorrow", offset_days=1)
        raw["time"].update(kind="clock", source="12", hour=12, minute=0, meridiem="unspecified")
        result = semantic.resolve(raw, "lunch tomorrow at 12", self.REFERENCE)
        self.assertEqual(result["start"], "2026-09-19T12:00:00+03:00")

    def test_clock_offset_is_resolved_deterministically(self):
        raw = empty()
        raw["date"].update(kind="relative", source="tomorrow", offset_days=1)
        raw["time"].update(
            kind="clock", source="quarter to six PM", hour=6, minute=0,
            meridiem="pm", offset_minutes=-15,
        )
        result = semantic.resolve(raw, "sync tomorrow quarter to six PM", self.REFERENCE)
        self.assertEqual(result["start"], "2026-09-19T17:45:00+03:00")

    def test_halfway_to_named_hour_uses_negative_offset(self):
        raw = empty()
        raw["date"].update(kind="relative", source="tomorrow", offset_days=1)
        raw["time"].update(
            kind="clock", source="в половине третьего", hour=3, minute=0,
            meridiem="unspecified", offset_minutes=-30,
        )
        result = semantic.resolve(raw, "sync tomorrow в половине третьего", self.REFERENCE)
        self.assertEqual(result["start"], "2026-09-19T02:30:00+03:00")

    def test_noon_and_midnight(self):
        for kind, expected in (("noon", "12:00:00"), ("midnight", "00:00:00")):
            with self.subTest(kind=kind):
                raw = empty(
                    date={
                        "kind": "relative", "source": "tomorrow", "year": None,
                        "month": None, "day": None, "weekday": None,
                        "offset_years": 0, "offset_months": 0,
                        "offset_weeks": 0, "offset_days": 1,
                    },
                    time={
                        "kind": kind, "source": kind, "hour": None, "minute": None,
                        "meridiem": "none", "offset_hours": 0,
                        "offset_minutes": 0, "approximate": False,
                    },
                )
                result = semantic.resolve(raw, f"sync tomorrow {kind}", self.REFERENCE)
                self.assertIn(expected, result["start"])

    def test_unused_meridiem_may_be_null(self):
        raw = empty()
        raw["date"].update(kind="relative", source="tomorrow", offset_days=1)
        raw["time"]["meridiem"] = None
        result = semantic.resolve(raw, "sync tomorrow", self.REFERENCE)
        self.assertNotIn("invalid_time", result["errors"])

    def test_correction_requires_confirmation(self):
        raw = empty(corrected=True)
        result = semantic.resolve(raw, "sync", self.REFERENCE)
        self.assertIn("self_correction", result["risks"])
        self.assertFalse(result["auto_write"])

    def test_non_create_operation_is_rejected(self):
        raw = empty(operation="edit")
        result = semantic.resolve(raw, "sync", self.REFERENCE)
        self.assertIn("invalid_operation", result["errors"])
        self.assertFalse(result["auto_write"])

    def test_malformed_text_field_is_rejected(self):
        raw = empty(location={"source": "at UTM", "text": "UTM"})
        result = semantic.resolve(raw, "sync at UTM", self.REFERENCE)
        self.assertEqual(result["location"], None)
        self.assertIn("invalid_location", result["errors"])
        self.assertFalse(result["auto_write"])

    def test_effect_stability_ignores_internal_type_only(self):
        left = {
            "auto_write": True, "operation": "create", "event_type": "appointment",
            "title": "documents", "start": "2026-10-14T09:00:00+03:00",
            "date": "2026-10-14", "all_day": False, "duration_minutes": 30,
            "location": None, "recurrence": None, "reminders_minutes": [120],
        }
        right = {**left, "event_type": "task"}
        self.assertTrue(semantic_benchmark.same_effect(left, right))
        self.assertFalse(semantic_benchmark.same_effect(left, {**right, "duration_minutes": 60}))

    def test_complex_details_require_confirmation(self):
        raw = empty(
            location="office",
            duration_minutes=30,
            duration_source="30 minutes",
            reminders=[{"source": "before", "minutes": 15}],
        )
        result = semantic.resolve(raw, "sync office 30 minutes before", self.REFERENCE)
        self.assertEqual(
            result["risks"], ["location"]
        )
        self.assertFalse(result["auto_write"])
        self.assertNotIn("explicit_duration", result["risks"])

    def test_grounded_weekday_duration_and_end_time_auto_write(self):
        raw = empty(
            date={
                "kind": "weekday", "source": "Friday", "year": None,
                "month": None, "day": None, "weekday": 4, "offset_years": 0,
                "offset_months": 0, "offset_weeks": 0, "offset_days": 0,
            },
            time={
                "kind": "clock", "source": "15:00", "hour": 15, "minute": 0,
                "meridiem": "24h", "offset_hours": 0,
                "offset_minutes": 0, "approximate": False,
            },
            duration_minutes=30,
            duration_source="30 minutes",
            end_time={
                "kind": "clock", "source": "16:00", "hour": 16, "minute": 0,
                "meridiem": "24h", "offset_hours": 0,
                "offset_minutes": 0, "approximate": False,
            },
        )
        result = semantic.resolve(
            raw, "sync Friday 15:00 to 16:00 for 30 minutes", self.REFERENCE
        )
        for risk in ("weekday", "explicit_duration", "end_time"):
            self.assertNotIn(risk, result["risks"])
        self.assertTrue(result["auto_write"])

    def test_one_grounded_reminder_can_auto_write(self):
        raw = empty(reminders=[{"source": "15 minutes before", "minutes": 15}])
        raw["date"].update(kind="relative", source="tomorrow", offset_days=1)
        raw["time"].update(kind="clock", source="9", hour=9, minute=0, meridiem="unspecified")
        result = semantic.resolve(raw, "sync tomorrow 9, 15 minutes before", self.REFERENCE)
        self.assertTrue(result["auto_write"])

    def test_multiple_reminders_require_confirmation(self):
        raw = empty(reminders=[
            {"source": "one hour before", "minutes": 60},
            {"source": "one day before", "minutes": 1440},
        ])
        result = semantic.resolve(raw, "sync one hour before and one day before", self.REFERENCE)
        self.assertIn("multiple_reminders", result["risks"])
        self.assertFalse(result["auto_write"])

    def test_missing_title_uses_type_fallback(self):
        raw = empty(title=None, event_type="task")
        raw["date"].update(kind="relative", source="tomorrow", offset_days=1)
        raw["time"].update(kind="clock", source="9", hour=9, minute=0, meridiem="unspecified")
        result = semantic.resolve(raw, "tomorrow at 9", self.REFERENCE)
        self.assertEqual(result["title"], "Task")
        self.assertTrue(result["auto_write"])

    def test_missing_title_uses_location_but_location_confirms(self):
        raw = empty(title=None, location="Starbucks")
        raw["date"].update(kind="relative", source="tomorrow", offset_days=1)
        raw["time"].update(kind="clock", source="9", hour=9, minute=0, meridiem="unspecified")
        result = semantic.resolve(raw, "Starbucks tomorrow at 9", self.REFERENCE)
        self.assertEqual(result["title"], "Starbucks")
        self.assertIn("location", result["risks"])
        self.assertFalse(result["auto_write"])

    def test_ungrounded_value_requires_confirmation(self):
        raw = empty(title="repaired title")
        result = semantic.resolve(raw, "garbled title", self.REFERENCE)
        self.assertIn("ungrounded_title", result["risks"])
        self.assertFalse(result["auto_write"])

    def test_malformed_evidence_is_risky_not_exception(self):
        raw = empty()
        raw["time"] = {
            "kind": "clock", "source": {"bad": "shape"}, "hour": 9, "minute": 0,
            "meridiem": "unspecified", "offset_hours": 0,
            "offset_minutes": 0, "approximate": False,
        }
        result = semantic.resolve(raw, "sync at 9", self.REFERENCE)
        self.assertIn("ungrounded_time", result["risks"])
        self.assertFalse(result["auto_write"])

    def test_relative_month_clamps_day(self):
        raw = empty(
            date={
                "kind": "relative", "source": "in one month", "year": None,
                "month": None, "day": None, "weekday": None, "offset_years": 0,
                "offset_months": 1, "offset_weeks": 0, "offset_days": 0,
            },
            time={
                "kind": "clock", "source": "9", "hour": 9, "minute": 0,
                "meridiem": "unspecified", "offset_hours": 0,
                "offset_minutes": 0, "approximate": False,
            },
        )
        result = semantic.resolve(raw, "sync in one month at 9", date(2026, 1, 31))
        self.assertEqual(result["date"], "2026-02-28")

    def test_relative_time_allows_zero_relative_date(self):
        raw = empty(
            date={
                "kind": "relative", "source": "in two hours", "year": None,
                "month": None, "day": None, "weekday": None, "offset_years": 0,
                "offset_months": 0, "offset_weeks": 0, "offset_days": 0,
            },
            time={
                "kind": "relative", "source": "in two hours", "hour": None,
                "minute": None, "meridiem": "none", "offset_hours": 2,
                "offset_minutes": 0, "approximate": False,
            },
        )
        result = semantic.resolve(raw, "sync in two hours", self.REFERENCE)
        self.assertEqual(result["start"], "2026-09-18T14:00:00+03:00")
        self.assertNotIn("relative_time_with_date", result["errors"])

    def test_weekly_recurrence_gets_first_date(self):
        raw = empty(
            time={
                "kind": "clock", "source": "8", "hour": 8, "minute": 0,
                "meridiem": "unspecified", "offset_hours": 0,
                "offset_minutes": 0, "approximate": False,
            },
            recurrence={
                "source": "every Monday", "freq": "weekly", "interval": 1,
                "weekdays": [0], "month_day": None, "month": None,
                "position": None, "count": None, "until": None,
            },
        )
        result = semantic.resolve(raw, "sync every Monday at 8", self.REFERENCE)
        self.assertEqual(result["start"], "2026-09-21T08:00:00+03:00")

    def test_end_time_crosses_midnight(self):
        raw = empty(
            date={
                "kind": "relative", "source": "tomorrow", "year": None,
                "month": None, "day": None, "weekday": None, "offset_years": 0,
                "offset_months": 0, "offset_weeks": 0, "offset_days": 1,
            },
            time={
                "kind": "clock", "source": "23:00", "hour": 23, "minute": 0,
                "meridiem": "24h", "offset_hours": 0, "offset_minutes": 0, "approximate": False,
            },
            end_time={
                "kind": "clock", "source": "1:00", "hour": 1, "minute": 0,
                "meridiem": "unspecified", "offset_hours": 0, "offset_minutes": 0, "approximate": False,
            },
        )
        result = semantic.resolve(raw, "sync tomorrow 23:00 to 1:00", self.REFERENCE)
        self.assertEqual(result["duration_minutes"], 120)

    def test_trip_defaults_to_next_local_midnight(self):
        raw = empty(event_type="trip")
        raw["date"].update(kind="relative", source="tomorrow", offset_days=1)
        raw["time"].update(kind="clock", source="11", hour=11, minute=0, meridiem="unspecified")
        result = semantic.resolve(raw, "trip tomorrow at 11", self.REFERENCE)
        self.assertEqual(result["duration_minutes"], 780)

    def test_explicit_two_day_trip_duration_wins(self):
        raw = empty(event_type="trip", duration_minutes=2880, duration_source="two days")
        raw["date"].update(kind="relative", source="tomorrow", offset_days=1)
        raw["time"].update(kind="clock", source="11", hour=11, minute=0, meridiem="unspecified")
        result = semantic.resolve(raw, "trip tomorrow at 11 for two days", self.REFERENCE)
        self.assertEqual(result["duration_minutes"], 2880)

    def test_null_end_time_keeps_simple_event_auto_writable(self):
        raw = empty(end_time=None)
        raw["date"].update(kind="relative", source="tomorrow", offset_days=1)
        raw["time"].update(kind="clock", source="9", hour=9, minute=0, meridiem="unspecified")
        result = semantic.resolve(raw, "sync tomorrow 9", self.REFERENCE)
        self.assertTrue(result["auto_write"])

    def test_monthly_ordinal_recurrence(self):
        raw = empty(
            time={
                "kind": "clock", "source": "9", "hour": 9, "minute": 0,
                "meridiem": "unspecified", "offset_hours": 0, "offset_minutes": 0, "approximate": False,
            },
            recurrence={
                "source": "second Tuesday", "freq": "monthly", "interval": 1,
                "weekdays": [1], "month_day": None, "month": None,
                "position": 2, "count": None, "until": None,
            },
        )
        result = semantic.resolve(raw, "sync second Tuesday at 9", self.REFERENCE)
        self.assertEqual(result["date"], "2026-10-13")

    def test_yearly_until_rejects_start_after_end(self):
        raw = empty(
            time={
                "kind": "clock", "source": "9", "hour": 9, "minute": 0,
                "meridiem": "unspecified", "offset_hours": 0, "offset_minutes": 0, "approximate": False,
            },
            recurrence={
                "source": "yearly", "freq": "yearly", "interval": 1,
                "weekdays": [], "month_day": 1, "month": 1,
                "position": None, "count": None, "until": "2026-12-31",
            },
        )
        result = semantic.resolve(raw, "sync yearly at 9", self.REFERENCE)
        self.assertIn("recurrence_before_start", result["errors"])

    def test_malformed_structures_never_auto_write(self):
        cases = (
            ("date", []),
            ("time", "9"),
            ("end_time", 9),
            ("recurrence", []),
            ("reminders", {}),
            ("all_day", 1),
            ("corrected", "yes"),
        )
        for field, value in cases:
            with self.subTest(field=field):
                result = semantic.resolve(empty(**{field: value}), "sync", self.REFERENCE)
                self.assertTrue(result["errors"])
                self.assertFalse(result["auto_write"])

    def test_malformed_nested_arrays_never_auto_write(self):
        recurrence = {
            "source": "weekly", "freq": "weekly", "interval": 1,
            "weekdays": "Monday", "month_day": None, "month": None,
            "position": None, "count": None, "until": None,
        }
        result = semantic.resolve(
            empty(recurrence=recurrence, reminders=["tomorrow"]),
            "sync weekly tomorrow",
            self.REFERENCE,
        )
        self.assertIn("invalid_recurrence", result["errors"])
        self.assertIn("invalid_reminder", result["errors"])
        self.assertFalse(result["auto_write"])

    def test_duration_requires_grounded_source(self):
        result = semantic.resolve(
            empty(duration_minutes=30, duration_source=None), "sync", self.REFERENCE
        )
        self.assertIn("ungrounded_duration", result["risks"])
        self.assertFalse(result["auto_write"])

    def test_monthly_day_uses_upcoming_current_month(self):
        recurrence = {
            "source": "monthly on 25", "freq": "monthly", "interval": 1,
            "weekdays": [], "month_day": 25, "month": None,
            "position": None, "count": None, "until": None,
        }
        result = semantic.resolve(
            empty(recurrence=recurrence), "sync monthly on 25", self.REFERENCE
        )
        self.assertEqual(result["date"], "2026-09-25")

    def test_monthly_ordinal_uses_upcoming_current_month(self):
        recurrence = {
            "source": "fourth Friday", "freq": "monthly", "interval": 1,
            "weekdays": [4], "month_day": None, "month": None,
            "position": 4, "count": None, "until": None,
        }
        result = semantic.resolve(
            empty(recurrence=recurrence), "sync fourth Friday", self.REFERENCE
        )
        self.assertEqual(result["date"], "2026-09-25")

    def test_monthly_passed_occurrence_uses_next_month(self):
        recurrence = {
            "source": "monthly on 10", "freq": "monthly", "interval": 1,
            "weekdays": [], "month_day": 10, "month": None,
            "position": None, "count": None, "until": None,
        }
        result = semantic.resolve(
            empty(recurrence=recurrence), "sync monthly on 10", self.REFERENCE
        )
        self.assertEqual(result["date"], "2026-10-10")

    def test_yearly_uses_current_or_next_year(self):
        for month, day, expected in ((12, 1, "2026-12-01"), (1, 1, "2027-01-01")):
            with self.subTest(month=month):
                recurrence = {
                    "source": "yearly", "freq": "yearly", "interval": 1,
                    "weekdays": [], "month_day": day, "month": month,
                    "position": None, "count": None, "until": None,
                }
                result = semantic.resolve(empty(recurrence=recurrence), "sync yearly", self.REFERENCE)
                self.assertEqual(result["date"], expected)

    def test_yearly_leap_day_finds_next_valid_occurrence(self):
        recurrence = {
            "source": "yearly", "freq": "yearly", "interval": 1,
            "weekdays": [], "month_day": 29, "month": 2,
            "position": None, "count": None, "until": None,
        }
        result = semantic.resolve(empty(recurrence=recurrence), "sync yearly", self.REFERENCE)
        self.assertEqual(result["date"], "2028-02-29")

    def test_contract_hash(self):
        self.assertEqual(len(semantic.contract_hash()), 12)


if __name__ == "__main__":
    unittest.main()
