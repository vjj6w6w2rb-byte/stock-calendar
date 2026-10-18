import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from eastern_calendar import calendar_text


class EasternCalendarTests(unittest.TestCase):
    def setUp(self):
        self.text = calendar_text(date(2026, 3, 6), datetime(2026, 1, 1, tzinfo=timezone.utc))

    def test_weekdays_have_exactly_two_events(self):
        for day in ("20260306", "20260309", "20260310"):
            self.assertEqual(self.text.count(f"DTSTART;TZID=America/New_York:{day}"), 2)
        self.assertNotIn("DTSTART;TZID=America/New_York:20260307", self.text)
        self.assertNotIn("DTSTART;TZID=America/New_York:20260308", self.text)

    def test_titles_and_local_times(self):
        self.assertIn("SUMMARY:美东 2026年3月9日 凌晨零点", self.text)
        self.assertIn("SUMMARY:美东 2026年3月9日 上午九时", self.text)
        self.assertIn("DTSTART;TZID=America/New_York:20260309T000000", self.text)
        self.assertIn("DTSTART;TZID=America/New_York:20260309T090000", self.text)

    def test_dst_and_uid_stability(self):
        a = calendar_text(date(2026, 10, 30), datetime(2026, 1, 1, tzinfo=timezone.utc))
        b = calendar_text(date(2026, 10, 30), datetime(2026, 2, 1, tzinfo=timezone.utc))
        uid = "UID:eastern-2026-11-02-09@eastern-time-calendar"
        self.assertIn(uid, a)
        self.assertIn(uid, b)
        self.assertIn("TZID:America/New_York", a)
        self.assertIn("RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU", a)

    def test_rfc_shape_and_utf8(self):
        self.assertTrue(self.text.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertTrue(self.text.endswith("END:VCALENDAR\r\n"))
        self.assertIn("TRANSP:TRANSPARENT", self.text)
        self.assertLessEqual(max(len(line.encode()) for line in self.text.split("\r\n")), 75)


if __name__ == "__main__":
    unittest.main()
