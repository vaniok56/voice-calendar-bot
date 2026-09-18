import unittest

import resolve


class TimeTests(unittest.TestCase):
    def test_numeric_and_meridiem(self):
        self.assertEqual(resolve.parse_time("5:54 in the evening")[:2], (17, 54))
        self.assertEqual(resolve.parse_time("1:00 PM")[:2], (13, 0))
        self.assertEqual(resolve.parse_time("10 am")[:2], (10, 0))
        self.assertEqual(resolve.parse_time("6 PM")[:2], (18, 0))

    def test_word_forms(self):
        self.assertEqual(resolve.parse_time("zece dimineața")[:2], (10, 0))
        self.assertEqual(resolve.parse_time("fără un sfert de șase seara")[:2], (17, 45))
        self.assertEqual(resolve.parse_time("la patru și un sfert")[:2], (4, 15))
        self.assertEqual(resolve.parse_time("două și jumătate")[:2], (2, 30))
        self.assertEqual(resolve.parse_time("ten in the morning")[:2], (10, 0))
        self.assertEqual(resolve.parse_time("five forty-five in the evening")[:2], (17, 45))

    def test_russian(self):
        self.assertEqual(resolve.parse_time("в половине третьего")[:2], (2, 30))
        self.assertEqual(resolve.parse_time("без четверти шесть вечера")[:2], (17, 45))
        self.assertEqual(resolve.parse_time("в 8 утра")[:2], (8, 0))

    def test_noon(self):
        self.assertEqual(resolve.parse_time("around noon")[:2], (12, 0))
        self.assertEqual(resolve.parse_time("примерно в полдень")[:2], (12, 0))

    def test_garbage_is_none(self):
        self.assertIsNone(resolve.parse_time("după masă"))


class DateTests(unittest.TestCase):
    def test_relative(self):
        self.assertEqual(str(resolve.parse_date("mâine")), "2026-09-19")
        self.assertEqual(str(resolve.parse_date("tomorrow")), "2026-09-19")
        self.assertEqual(str(resolve.parse_date("завтра")), "2026-09-19")

    def test_weekday_is_next_occurrence(self):
        self.assertEqual(str(resolve.parse_date("Marți")), "2026-09-22")
        self.assertEqual(str(resolve.parse_date("joi")), "2026-09-24")
        self.assertEqual(str(resolve.parse_date("Во вторник")), "2026-09-22")
        self.assertEqual(str(resolve.parse_date("Friday")), "2026-09-25")

    def test_explicit_month(self):
        self.assertEqual(str(resolve.parse_date("14 octombrie")), "2026-10-14")
        self.assertEqual(str(resolve.parse_date("October fourteenth")), "2026-10-14")
        self.assertEqual(str(resolve.parse_date("14 октября")), "2026-10-14")


class OffsetTests(unittest.TestCase):
    def test_durations(self):
        self.assertEqual(resolve._parse_offset("o oră"), 60)
        self.assertEqual(resolve._parse_offset("nouăzeci de minute"), 90)
        self.assertEqual(resolve._parse_offset("на один час"), 60)
        self.assertEqual(resolve._parse_offset("nineteen minutes"), 19)

    def test_reminders(self):
        self.assertEqual(resolve._parse_offset("cu treizeci de minute înainte"), 30)
        self.assertEqual(resolve._parse_offset("cu o zi înainte"), 1440)
        self.assertEqual(resolve._parse_offset("за час"), 60)
        self.assertEqual(resolve._parse_offset("two hours before"), 120)


class ResolveTests(unittest.TestCase):
    def test_weekly_recurrence_supplies_the_date(self):
        event = resolve.resolve({
            "operation": "create", "event_type": "class", "title": "English class",
            "date_text": None, "time_text": "eight in the morning",
            "duration_text": "nineteen minutes", "end_time_text": None,
            "location_text": None, "recurrence_text": "Every Monday", "reminder_texts": [],
        })
        self.assertTrue(event["complete"])
        self.assertEqual(event["start"], "2026-09-21T08:00")
        self.assertEqual(event["duration_minutes"], 19)

    def test_end_time_sets_duration(self):
        event = resolve.resolve({
            "operation": "create", "event_type": "meeting", "title": None,
            "date_text": "mâine", "time_text": "zece dimineața",
            "duration_text": None, "end_time_text": "unsprezece",
            "location_text": None, "recurrence_text": None, "reminder_texts": [],
        })
        self.assertEqual(event["start"], "2026-09-19T10:00")
        self.assertEqual(event["duration_minutes"], 60)

    def test_birthday_is_all_day(self):
        event = resolve.resolve({
            "operation": "create", "event_type": "birthday", "title": None,
            "date_text": "mâine", "time_text": None, "duration_text": None,
            "end_time_text": None, "location_text": None, "recurrence_text": None,
            "reminder_texts": [],
        })
        self.assertTrue(event["all_day"])
        self.assertTrue(event["complete"])
        self.assertIsNone(event["start"])

    def test_unresolvable_span_is_reported_not_guessed(self):
        event = resolve.resolve({
            "operation": "create", "event_type": "meeting", "title": None,
            "date_text": "whenever", "time_text": None, "duration_text": None,
            "end_time_text": None, "location_text": None, "recurrence_text": None,
            "reminder_texts": [],
        })
        self.assertFalse(event["complete"])
        self.assertEqual(event["unresolved"], ["date_text"])


class CorpusTests(unittest.TestCase):
    def test_every_reference_span_resolves(self):
        import run

        for case in run.load_cases():
            expected = case["expected"]
            event = resolve.resolve(expected)
            resolvable = any(
                expected.get(field)
                for field in ("date_text", "time_text", "recurrence_text")
            )
            if resolvable:
                self.assertTrue(event["complete"], case["id"])
                self.assertEqual(event["unresolved"], [], case["id"])


if __name__ == "__main__":
    unittest.main()
