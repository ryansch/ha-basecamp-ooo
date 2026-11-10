"""Tests for stretch building and run decision logic."""

from datetime import date

from custom_components.basecamp_ooo.stretches import (
    Decision,
    OutOfOffice,
    Result,
    Stretch,
    build_stretches,
    current_or_next,
    days_off,
    decide,
)

PERSON_ID = 9990004
OTHER_ID = 11111111


def entry(id, start, end, all_day=True, participants=(PERSON_ID,), recurrence=None):
    """Build a schedule entry dict shaped like the Basecamp API."""
    return {
        "id": id,
        "title": "Ryan - OOO",
        "all_day": all_day,
        "starts_at": start,
        "ends_at": end,
        "creator": {"id": PERSON_ID},
        "participants": [{"id": pid, "name": "Someone"} for pid in participants],
        "recurrence_schedule": recurrence,
    }


def stretches_for(*entries):
    return build_stretches(days_off(list(entries), PERSON_ID))


def test_counts_entry_where_person_is_participant_regardless_of_creator_and_title():
    e = entry(1, "2026-11-11", "2026-11-11", participants=(OTHER_ID, PERSON_ID))
    e["creator"] = {"id": OTHER_ID}
    e["title"] = "Team offsite"

    assert days_off([e], PERSON_ID) == {date(2026, 11, 11): {1}}


def test_ignores_entry_without_person():
    e = entry(1, "2026-11-11", "2026-11-11", participants=(OTHER_ID,))

    assert days_off([e], PERSON_ID) == {}


def test_ignores_entry_missing_participants_key():
    e = entry(1, "2026-11-11", "2026-11-11")
    del e["participants"]

    assert days_off([e], PERSON_ID) == {}


def test_ignores_timed_entries():
    e = entry(1, "2026-11-11T13:00:00.000Z", "2026-11-11T14:00:00.000Z", all_day=False)

    assert days_off([e], PERSON_ID) == {}


def test_ignores_recurring_entries():
    e = entry(1, "2026-11-11", "2026-11-11", recurrence={"frequency": "every_week"})

    assert days_off([e], PERSON_ID) == {}


def test_missing_recurrence_key_counts_as_non_recurring():
    e = entry(1, "2026-11-11", "2026-11-11")
    del e["recurrence_schedule"]

    assert days_off([e], PERSON_ID) == {date(2026, 11, 11): {1}}


def test_ignores_entry_with_end_before_start():
    e = entry(1, "2026-11-13", "2026-11-11")

    assert days_off([e], PERSON_ID) == {}


def test_multi_day_entry_expands_inclusive():
    e = entry(1, "2026-11-11", "2026-11-13")

    assert days_off([e], PERSON_ID) == {
        date(2026, 11, 11): {1},
        date(2026, 11, 12): {1},
        date(2026, 11, 13): {1},
    }


def test_overlapping_entries_collect_all_ids_per_date():
    days = days_off(
        [entry(1, "2026-11-11", "2026-11-12"), entry(2, "2026-11-12", "2026-11-12")],
        PERSON_ID,
    )

    assert days[date(2026, 11, 12)] == {1, 2}


def test_touching_and_overlapping_entries_merge():
    stretches = stretches_for(
        entry(1, "2026-11-12", "2026-11-12"),  # Thu
        entry(2, "2026-11-13", "2026-11-13"),  # Fri
        entry(3, "2026-11-12", "2026-11-13"),  # overlaps both
    )

    assert stretches == [Stretch(date(2026, 11, 12), date(2026, 11, 13), (1, 2, 3))]


def test_weekend_bridges_fri_and_mon():
    stretches = stretches_for(
        entry(1, "2026-11-13", "2026-11-13"), entry(2, "2026-11-16", "2026-11-16")
    )

    assert stretches == [Stretch(date(2026, 11, 13), date(2026, 11, 16), (1, 2))]


def test_stretch_ending_friday_not_extended():
    stretches = stretches_for(entry(1, "2026-11-13", "2026-11-13"))

    assert stretches == [Stretch(date(2026, 11, 13), date(2026, 11, 13), (1,))]


def test_monday_start_not_pulled_back():
    stretches = stretches_for(entry(1, "2026-11-16", "2026-11-16"))

    assert stretches == [Stretch(date(2026, 11, 16), date(2026, 11, 16), (1,))]


def test_weekday_gap_does_not_bridge():
    stretches = stretches_for(
        entry(1, "2026-11-16", "2026-11-16"), entry(2, "2026-11-18", "2026-11-18")
    )

    assert stretches == [
        Stretch(date(2026, 11, 16), date(2026, 11, 16), (1,)),
        Stretch(date(2026, 11, 18), date(2026, 11, 18), (2,)),
    ]


def test_build_stretches_sorted_by_start_regardless_of_entry_order():
    stretches = stretches_for(
        entry(2, "2026-11-18", "2026-11-18"), entry(1, "2026-11-16", "2026-11-16")
    )

    assert [s.start for s in stretches] == [date(2026, 11, 16), date(2026, 11, 18)]


def test_current_or_next_returns_first_stretch_not_yet_ended():
    past = Stretch(date(2026, 11, 2), date(2026, 11, 3), (1,))
    current = Stretch(date(2026, 11, 9), date(2026, 11, 11), (2,))
    future = Stretch(date(2026, 11, 20), date(2026, 11, 20), (3,))

    assert current_or_next([past, current, future], date(2026, 11, 11)) == current
    assert current_or_next([past, current, future], date(2026, 11, 12)) == future
    assert current_or_next([past], date(2026, 11, 12)) is None


DISABLED = OutOfOffice(enabled=False, start=None, end=None)
STRETCH = Stretch(date(2026, 11, 13), date(2026, 11, 16), (1, 2))


def test_decide_no_time_off_today():
    assert decide(date(2026, 11, 12), [STRETCH], DISABLED) == Decision(
        Result.NO_TIME_OFF_TODAY, None
    )


def test_decide_mid_stretch():
    assert decide(date(2026, 11, 16), [STRETCH], DISABLED) == Decision(
        Result.MID_STRETCH, STRETCH
    )


def test_decide_first_day_sets_when_ooo_disabled():
    decision = decide(date(2026, 11, 13), [STRETCH], DISABLED)

    assert decision.result is Result.SET
    assert decision.stretch == STRETCH
    assert decision.stretch.end == date(2026, 11, 16)


def test_decide_first_day_already_active():
    ooo = OutOfOffice(enabled=True, start=date(2026, 11, 13), end=date(2026, 11, 16))

    assert decide(date(2026, 11, 13), [STRETCH], ooo) == Decision(
        Result.ALREADY_ACTIVE, STRETCH
    )


def test_decide_first_day_existing_future_ooo_skipped():
    ooo = OutOfOffice(enabled=True, start=date(2026, 12, 1), end=date(2026, 12, 5))

    assert decide(date(2026, 11, 13), [STRETCH], ooo) == Decision(
        Result.SKIPPED_EXISTING_FUTURE, STRETCH
    )


def test_result_values_match_global_constraints():
    assert [r.value for r in Result] == [
        "no_time_off_today",
        "mid_stretch",
        "already_active",
        "skipped_existing_future",
        "set",
        "error",
    ]


def test_out_of_office_from_api_disabled_omits_dates():
    payload = {"person": {"id": PERSON_ID}, "enabled": False, "ongoing": False}

    assert OutOfOffice.from_api(payload) == OutOfOffice(
        enabled=False, start=None, end=None
    )


def test_out_of_office_from_api_enabled_parses_dates():
    payload = {
        "person": {"id": PERSON_ID},
        "enabled": True,
        "ongoing": True,
        "start_date": "2026-11-13",
        "end_date": "2026-11-16",
        "back_on_date": "2026-11-17",
    }

    assert OutOfOffice.from_api(payload) == OutOfOffice(
        enabled=True, start=date(2026, 11, 13), end=date(2026, 11, 16)
    )
