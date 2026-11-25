"""Week schedules, without Home Assistant.

A week maps day keys (mon..sun) to points {"at": "HH:MM", "value": temperature | "off"}. Each
point holds until the next one: the last point of a day runs on into the next day, so a day
without points continues the day before.

A point may also set slot parameters, {"params": {parameter id: bool | number}} (an eco mode
switch, a hysteresis number…). A parameter's value holds until a later point changes it, round
the week like the temperature: a point without it keeps the value before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


def validate_param_value(value: Any) -> bool | float:
    """A slot parameter's value: a bool (switch) or a number."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    raise ValueError(f"invalid parameter value {value!r}")


def validate_day(points: Any) -> list[dict[str, Any]]:
    if not isinstance(points, list):
        raise ValueError("a day is a list of points")
    out: dict[int, dict[str, Any]] = {}
    for point in points:
        if not isinstance(point, dict) or set(point) - {"at", "value", "params"}:
            raise ValueError(f"invalid point {point!r}")
        at = parse_time(point.get("at"))
        if at in out:
            raise ValueError(f"two points at {format_time(at)}")
        clean: dict[str, Any] = {"at": format_time(at), "value": validate_value(point.get("value"))}
        params = point.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError(f"invalid parameters {params!r}")
        if params:
            clean["params"] = {str(k): validate_param_value(v) for k, v in params.items()}
        out[at] = clean
    return [out[at] for at in sorted(out)]


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


def week_points(week: dict[str, list[dict[str, Any]]]) -> list[tuple[int, Value, dict[str, Any]]]:
    """All points of a week as (minute of the week, value, parameters in force), Monday 00:00 = 0.

    The parameters in force carry the values set by earlier points, round the week (a value set on
    Sunday evening still holds on Monday morning)."""
    raw = []
    for index, day in enumerate(DAYS):
        for point in week.get(day, []):
            raw.append((index * DAY_MINUTES + parse_time(point["at"]), point["value"], point.get("params") or {}))
    raw.sort(key=lambda p: p[0])
    params: dict[str, Any] = {}
    points: list[tuple[int, Value, dict[str, Any]]] = []
    for _ in range(2):  # the second round starts with what the end of the week set
        points = []
        for minute, value, own in raw:
            params = {**params, **own}
            points.append((minute, value, dict(params)))
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
    params: dict[str, Any] = field(default_factory=dict)


def evaluate(week: dict[str, list[dict[str, Any]]] | None, now: datetime) -> Slot:
    """The slot at `now` (an aware local datetime). Empty week: no value."""
    points = week_points(week or {})
    if not points:
        return Slot(None)
    now_minute = now.weekday() * DAY_MINUTES + now.hour * 60 + now.minute
    current, params = points[-1][1], points[-1][2]  # before the first point of the week: last week's last point
    for minute, value, in_force in points:
        if minute > now_minute:
            break
        current, params = value, in_force
    later = [p for p in points if p[0] > now_minute] + [(m + WEEK_MINUTES, v, p) for m, v, p in points]
    for minute, value, in_force in later:
        if not same_value(value, current) or not same_params(in_force, params):
            return Slot(current, _local(now, minute), value, params)
    return Slot(current, params=params)


def same_params(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if set(a) != set(b):
        return False
    for key, va in a.items():
        vb = b[key]
        if isinstance(va, bool) or isinstance(vb, bool):
            if va is not vb:
                return False
        elif abs(float(va) - float(vb)) > 1e-6:
            return False
    return True


def _local(now: datetime, week_minute: int) -> datetime:
    """The local datetime of a minute of now's week (may be past Sunday: following weeks)."""
    monday = now.date() - timedelta(days=now.weekday())
    day = monday + timedelta(days=week_minute // DAY_MINUTES)
    minute = week_minute % DAY_MINUTES
    return datetime.combine(day, time(minute // 60, minute % 60), tzinfo=now.tzinfo)
