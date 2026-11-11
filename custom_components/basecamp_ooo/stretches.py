"""Pure logic: turn schedule entries into time-off stretches and decide a run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

SATURDAY = 5


@dataclass(frozen=True)
class Stretch:
    """A run of days off, bridged across weekends. Start and end are days off."""

    start: date
    end: date
    entry_ids: tuple[int, ...]


@dataclass(frozen=True)
class OutOfOffice:
    """The person's current Basecamp out-of-office setting."""

    enabled: bool
    start: date | None
    end: date | None

    @classmethod
    def from_api(cls, payload: dict) -> OutOfOffice:
        """Build from an out_of_office.json payload (dates absent when disabled)."""
        return cls(
            enabled=bool(payload.get("enabled")),
            start=_parse_date(payload.get("start_date")),
            end=_parse_date(payload.get("end_date")),
        )


class Result(StrEnum):
    """Outcome of a run."""

    NO_TIME_OFF_TODAY = "no_time_off_today"
    MID_STRETCH = "mid_stretch"
    ALREADY_ACTIVE = "already_active"
    SKIPPED_EXISTING_FUTURE = "skipped_existing_future"
    SET = "set"
    ERROR = "error"


@dataclass(frozen=True)
class Decision:
    """What a run should do, and the stretch it concerns (if any)."""

    result: Result
    stretch: Stretch | None


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def days_off(entries: list[dict], person_id: int) -> dict[date, set[int]]:
    """Map each day off to the IDs of the entries that make it a day off.

    Counts non-recurring, active, all-day entries the person participates in.
    """
    days: dict[date, set[int]] = {}
    for entry in entries:
        if entry.get("status", "active") != "active":
            continue  # defensive: the API lists only active entries by default
        if not entry.get("all_day") or entry.get("recurrence_schedule"):
            continue
        if not any(p.get("id") == person_id for p in entry.get("participants") or []):
            continue
        entry_id = entry.get("id")
        if entry_id is None:
            continue
        try:
            start = date.fromisoformat(entry["starts_at"][:10])
            end = date.fromisoformat(entry["ends_at"][:10])
        except KeyError, TypeError, ValueError:
            continue  # malformed entry: skip it rather than fail the run
        day = start
        while day <= end:
            days.setdefault(day, set()).add(entry_id)
            day += timedelta(days=1)
    return days


def _only_weekend_between(earlier: date, later: date) -> bool:
    day = earlier + timedelta(days=1)
    while day < later:
        if day.weekday() < SATURDAY:
            return False
        day += timedelta(days=1)
    return True


def build_stretches(days: dict[date, set[int]]) -> list[Stretch]:
    """Group days off into stretches, bridging gaps made only of weekend days."""
    groups: list[list[date]] = []
    for day in sorted(days):
        if groups and _only_weekend_between(groups[-1][-1], day):
            groups[-1].append(day)
        else:
            groups.append([day])
    return [
        Stretch(
            start=group[0],
            end=group[-1],
            entry_ids=tuple(sorted(set().union(*(days[d] for d in group)))),
        )
        for group in groups
    ]


def current_or_next(stretches: list[Stretch], today: date) -> Stretch | None:
    """Return the first stretch that has not ended before today."""
    return next((s for s in stretches if s.end >= today), None)


def decide(today: date, stretches: list[Stretch], ooo: OutOfOffice) -> Decision:
    """Decide what a run on `today` should do."""
    stretch = next((s for s in stretches if s.start <= today <= s.end), None)
    if stretch is None:
        return Decision(Result.NO_TIME_OFF_TODAY, None)
    if today != stretch.start:
        return Decision(Result.MID_STRETCH, stretch)
    if ooo.start and ooo.end:
        # Dated OOO decides on its dates, not just the enabled flag: an expired
        # one is absent, and a current or future one is never overwritten.
        if ooo.end < today:
            return Decision(Result.SET, stretch)
        if ooo.start <= today:
            return Decision(Result.ALREADY_ACTIVE, stretch)
        return Decision(Result.SKIPPED_EXISTING_FUTURE, stretch)
    if ooo.enabled:
        return Decision(Result.SKIPPED_EXISTING_FUTURE, stretch)
    return Decision(Result.SET, stretch)
