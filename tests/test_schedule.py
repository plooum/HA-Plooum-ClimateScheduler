"""Tests of the week schedules (schedule.py), without Home Assistant.

Run: python -m unittest discover tests
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "plooum_climate_scheduler" / "schedule.py"
spec = importlib.util.spec_from_file_location("climate_schedule", SRC)
schedule = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = schedule  # for its dataclass
spec.loader.exec_module(schedule)

PARIS = ZoneInfo("Europe/Paris")
OFF = schedule.OFF


def at(y, mo, d, h, mi=0, tz=PARIS):
    return datetime(y, mo, d, h, mi, tzinfo=tz)


# The owner's example (Monday 2026-11-23): living room 17 until 17:00 then 21.
SALON = {"mon": [{"at": "00:00", "value": 17}, {"at": "17:00", "value": 21}]}
# Bedrooms: 17 until 08:00, 15 until 18:00, then 20.
BEDROOMS = {
    day: [{"at": "00:00", "value": 17}, {"at": "08:00", "value": 15}, {"at": "18:00", "value": 20}]
    for day in schedule.DAYS
}


class Validation(unittest.TestCase):
    def test_sorts_and_fills_days(self):
        week = schedule.validate_week({"tue": [{"at": "18:00", "value": "20"}, {"at": "06:30", "value": OFF}]})
        self.assertEqual(week["tue"], [{"at": "06:30", "value": OFF}, {"at": "18:00", "value": 20.0}])
        self.assertEqual(week["mon"], [])
        self.assertEqual(list(week), list(schedule.DAYS))

    def test_rejects(self):
        for bad in (
            {"monday": []},
            {"mon": [{"at": "24:00", "value": 20}]},
            {"mon": [{"at": "7:00", "value": 20}]},
            {"mon": [{"at": "07:00", "value": "warm"}]},
            {"mon": [{"at": "07:00", "value": True}]},
            {"mon": [{"at": "07:00", "value": 400}]},
            {"mon": [{"at": "07:00", "value": 20}, {"at": "07:00", "value": 21}]},
            {"mon": [{"at": "07:00", "value": 20, "extra": 1}]},
            {"mon": {"at": "07:00"}},
            [],
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                schedule.validate_week(bad)

    def test_resolve_falls_back_to_default(self):
        profiles = {"default": SALON, "holidays": BEDROOMS}
        self.assertEqual(schedule.resolve_week(profiles, "holidays"), ("holidays", BEDROOMS))
        self.assertEqual(schedule.resolve_week(profiles, "away"), ("default", SALON))
        self.assertEqual(schedule.resolve_week({}, "away"), (None, None))


class Evaluate(unittest.TestCase):
    def test_owner_example(self):
        slot = schedule.evaluate(BEDROOMS, at(2026, 11, 23, 9, 15))
        self.assertEqual(slot, schedule.Slot(15, at(2026, 11, 23, 18), 20))
        slot = schedule.evaluate(BEDROOMS, at(2026, 11, 23, 18))  # a point holds from its minute
        self.assertEqual(slot, schedule.Slot(20, at(2026, 11, 24, 0), 17))

    def test_day_without_points_continues_the_day_before(self):
        # Only Monday: 21 from 17:00 runs on until next Monday 00:00.
        slot = schedule.evaluate(SALON, at(2026, 11, 26, 12))  # Thursday
        self.assertEqual(slot, schedule.Slot(21, at(2026, 11, 30, 0), 17))

    def test_before_first_point_of_the_week(self):
        week = {"wed": [{"at": "06:00", "value": 19}], "fri": [{"at": "22:00", "value": OFF}]}
        slot = schedule.evaluate(week, at(2026, 11, 23, 8))  # Monday: still Friday's off
        self.assertEqual(slot, schedule.Slot(OFF, at(2026, 11, 25, 6), 19))
        slot = schedule.evaluate(week, at(2026, 11, 28, 8))  # Saturday: wraps to next Wednesday
        self.assertEqual(slot, schedule.Slot(OFF, at(2026, 12, 2, 6), 19))

    def test_next_change_skips_points_with_the_same_value(self):
        week = {d: [{"at": "00:00", "value": 18}, {"at": "12:00", "value": 18.0}] for d in schedule.DAYS}
        self.assertEqual(schedule.evaluate(week, at(2026, 11, 23, 1)), schedule.Slot(18))
        week["sun"].append({"at": "20:00", "value": 21})
        self.assertEqual(
            schedule.evaluate(week, at(2026, 11, 23, 1)), schedule.Slot(18, at(2026, 11, 29, 20), 21)
        )

    def test_empty(self):
        self.assertEqual(schedule.evaluate({}, at(2026, 11, 23, 1)), schedule.Slot(None))
        self.assertEqual(schedule.evaluate(None, at(2026, 11, 23, 1)), schedule.Slot(None))

    def test_spring_gap(self):
        # 2026-03-29 02:00 → 03:00 in Paris. A point at 02:30 fires at 03:30 local (01:30 UTC).
        week = {"sun": [{"at": "00:00", "value": 17}, {"at": "02:30", "value": 21}]}
        slot = schedule.evaluate(week, at(2026, 3, 29, 1))
        self.assertEqual(slot.next_change.astimezone(timezone.utc), datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc))
        self.assertEqual(schedule.evaluate(week, at(2026, 3, 29, 3, 45)).value, 21)

    def test_autumn_change(self):
        # 2026-10-25 03:00 → 02:00. 08:00 local is then 07:00 UTC.
        slot = schedule.evaluate(BEDROOMS, at(2026, 10, 25, 1))
        self.assertEqual(slot.next_change.astimezone(timezone.utc), datetime(2026, 10, 25, 7, tzinfo=timezone.utc))

    def test_next_change_is_always_later(self):
        now = at(2026, 11, 23, 0)
        for minutes in range(0, 7 * 24 * 60, 37):
            t = now + timedelta(minutes=minutes)
            slot = schedule.evaluate(BEDROOMS, t)
            self.assertGreater(slot.next_change, t)
            self.assertLessEqual(slot.next_change - t, timedelta(hours=10))


if __name__ == "__main__":
    unittest.main()
