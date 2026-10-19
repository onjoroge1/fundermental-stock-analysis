"""Earnings announcements from SEC 8-K item 2.02: point-in-time, every filing an event."""

import pytest

from stock_machine.events import earnings_announcements as ea


def block(*rows):
    keys = ("accessionNumber", "form", "filingDate", "acceptanceDateTime", "items")
    return {k: [r[i] for r in rows] for i, k in enumerate(keys)}


RECENT = {"filings": {"recent": block(
    ("0001-26-000003", "8-K", "2026-07-30", "2026-07-30T20:05:11.000Z", "2.02,9.01"),
    ("0001-26-000002", "10-Q", "2026-07-31", "2026-07-31T14:00:00.000Z", ""),
    ("0001-26-000001", "8-K", "2026-07-10", "2026-07-10T12:00:00.000Z", "5.02,2.02"),
)}}
PAGE = block(
    ("0001-26-000001", "8-K", "2026-07-10", "2026-07-10T12:00:00.000Z", "5.02,2.02"),   # duplicate of recent
    ("0001-26-000000", "8-K", "2026-04-28", "2026-04-28T13:30:00.000Z", "2.02"),
    ("0001-26-00000x", "8-K", "2026-03-02", "2026-03-02T13:30:00.000Z", "8.01"),
)


def test_pages_merge_and_deduplicate_by_accession():
    records = ea.filings([RECENT, PAGE, {"unrelated": True}])
    assert [r["accessionNumber"] for r in records] == [
        "0001-26-000003", "0001-26-000002", "0001-26-000001", "0001-26-000000", "0001-26-00000x"]


def test_every_item_202_filing_is_an_event_with_a_backward_looking_role():
    events = ea.announcements("X", "0001", ea.filings([RECENT, PAGE]))
    assert [e["accession"] for e in events] == ["0001-26-000000", "0001-26-000001", "0001-26-000003"]
    # 2026-07-10 is 73 days after April; 2026-07-30 follows it by 20 days.
    assert [e["role"] for e in events] == ["PRIMARY", "PRIMARY", "CLUSTERED"]
    assert events[1]["items"] == "5.02,2.02"


def test_the_reaction_session_is_the_first_close_after_acceptance():
    events = {e["accession"]: e for e in ea.announcements("X", "0001", ea.filings([RECENT, PAGE]))}
    assert events["0001-26-000000"]["reaction_session"] == "2026-04-28"   # 09:30 EDT, before the close
    assert events["0001-26-000003"]["reaction_session"] == "2026-07-31"   # 16:05 EDT, after the close
    assert events["0001-26-000003"]["acceptance_utc"] == "2026-07-30T20:05:11+00:00"


def test_the_window_bounds_use_the_acceptance_date():
    records = ea.filings([RECENT, PAGE])
    assert [e["accession"] for e in ea.announcements("X", "0001", records, until="2026-07-10")] == [
        "0001-26-000000", "0001-26-000001"]
    assert [e["accession"] for e in ea.announcements("X", "0001", records, since="2026-07-11")] == [
        "0001-26-000003"]


def test_an_acceptance_time_without_a_zone_is_refused():
    bad = {"filings": {"recent": block(("a", "8-K", "2026-07-30", "2026-07-30T20:05:11", "2.02"))}}
    with pytest.raises(ValueError, match="SEC_ACCEPTANCE_WITHOUT_ZONE"):
        ea.announcements("X", "0001", ea.filings([bad]))


def test_roles_are_relabelled_across_a_registrant_change():
    predecessor = {"filings": {"recent": block(("p", "8-K", "2026-06-25", "2026-06-25T20:01:00.000Z", "2.02"))}}
    old = ea.announcements("X", "0009", ea.filings([predecessor]), until="2026-06-30")
    new = ea.announcements("X", "0001", ea.filings([RECENT]))
    assert new[0]["role"] == "PRIMARY"
    merged = ea.label_roles(old + new)                 # 2026-07-10 follows the predecessor's release by 15 days
    assert [(e["cik"], e["role"]) for e in merged] == [("0009", "PRIMARY"), ("0001", "CLUSTERED"),
                                                        ("0001", "CLUSTERED")]


def test_validation_reports_thin_years_and_missing_reviewed_releases():
    events = ea.announcements("X", "0001", ea.filings([RECENT, PAGE]))
    out = ea.validate(events, reviewed_accessions={"X": ["0001-26-000003", "missing"]},
                      first_year={"X": 2025}, last_year=2027)
    assert out["years_with_fewer_than_3_primary"] == {"X": {"2025": 0, "2026": 2}}
    assert out["reviewed_release_accessions_missing"] == {"X": ["missing"]}
    assert out["clustered_events"] == 1 and out["primary_events"] == 2
