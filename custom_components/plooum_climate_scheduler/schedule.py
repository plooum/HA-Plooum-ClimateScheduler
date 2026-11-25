"""Week schedules, without Home Assistant.

A week maps day keys (mon..sun) to points {"at": "HH:MM", "value": temperature | "off"}. Each
point holds until the next one: the last point of a day runs on into the next day, so a day
without points continues the day before.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
import math
import re
from typing import Any

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
OFF = "off"
DEFAULT_PROFILE = "default"
DAY_MINUTES = 24 * 60
WEEK_MINUTES = 7 * DAY_MINUTES
MIN_TEMPERATURE = -50.0
MAX_TEMPERATURE = 100.0

_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

Value = float | str


def parse_time(text: Any) -> int:
    """'HH:MM' -> minutes since midnight."""
    m = _TIME.match(text) if isinstance(text, str) else None
    if not m:
        raise ValueError(f"invalid time {text!r}, expected HH:MM")
    return int(m.group(1)) * 60 + int(m.group(2))


def format_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def validate_value(value: Any) -> Value:
    if value == OFF:
        return OFF
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"invalid value {value!r}")
    try:
        temp = float(value)
    except ValueError as err:
        raise ValueError(f"invalid value {value!r}") from err
    if not math.isfinite(temp) or not MIN_TEMPERATURE <= temp <= MAX_TEMPERATURE:
        raise ValueError(f"temperature out of range: {value!r}")
    return round(temp, 2)


def validate_day(points: Any) -> list[dict[str, Any]]:
    if not isinstance(points, list):
        raise ValueError("a day is a list of points")
    out: dict[int, Value] = {}
    for point in points:
        if not isinstance(point, dict) or set(point) - {"at", "value"}:
            raise ValueError(f"invalid point {point!r}")
        at = parse_time(point.get("at"))
        if at in out:
            raise ValueError(f"two points at {format_time(at)}")
        out[at] = validate_value(point.get("value"))
    return [{"at": format_time(at), "value": out[at]} for at in sorted(out)]


def validate_week(week: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(week, dict):
        raise ValueError("a week is an object of days")
    unknown = set(week) - set(DAYS)
    if unknown:
        raise ValueError(f"unknown days: {', '.join(sorted(unknown))}")
    return {day: validate_day(week.get(day, [])) for day in DAYS}


def resolve_week(profiles: dict[str, Any], profile_id: str) -> tuple[str | None, dict | None]:
    """The week a program uses under a profile: its own, else its default one."""
    if profile_id in profiles:
        return profile_id, profiles[profile_id]
    if DEFAULT_PROFILE in profiles:
        return DEFAULT_PROFILE, profiles[DEFAULT_PROFILE]
    return None, None


def week_points(week: dict[str, list[dict[str, Any]]]) -> list[tuple[int, Value]]:
    """All points of a week as (minute of the week, value), Monday 00:00 = 0."""
    points = []
    for index, day in enumerate(DAYS):
        for point in week.get(day, []):
            points.append((index * DAY_MINUTES + parse_time(point["at"]), point["value"]))
    points.sort(key=lambda p: p[0])
    return points


def same_value(a: Value | None, b: Value | None) -> bool:
    if a == OFF or b == OFF or a is None or b is None:
        return a == b
    return abs(float(a) - float(b)) < 1e-6


@dataclass(frozen=True)
class Slot:
    """What a week says at an instant: the value now, and the next change."""

    value: Value | None
    next_change: datetime | None = None
    next_value: Value | None = None


def evaluate(week: dict[str, list[dict[str, Any]]] | None, now: datetime) -> Slot:
    """The slot at `now` (an aware local datetime). Empty week: no value."""
    points = week_points(week or {})
    if not points:
        return Slot(None)
    now_minute = now.weekday() * DAY_MINUTES + now.hour * 60 + now.minute
    current = points[-1][1]  # before the first point of the week: last week's last point
    for minute, value in points:
        if minute > now_minute:
            break
        current = value
    later = [p for p in points if p[0] > now_minute] + [(m + WEEK_MINUTES, v) for m, v in points]
    for minute, value in later:
        if not same_value(value, current):
            return Slot(current, _local(now, minute), value)
    return Slot(current)


def _local(now: datetime, week_minute: int) -> datetime:
    """The local datetime of a minute of now's week (may be past Sunday: following weeks)."""
    monday = now.date() - timedelta(days=now.weekday())
    day = monday + timedelta(days=week_minute // DAY_MINUTES)
    minute = week_minute % DAY_MINUTES
    return datetime.combine(day, time(minute // 60, minute % 60), tzinfo=now.tzinfo)
