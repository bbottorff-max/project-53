import json

import pandas as pd
import pytest

from project53.data import pipeline as p


def csv(rows):
    return ("datetime,open,high,low,close,volume\n" + "\n".join(rows) + "\n").encode()


def bars(times=("2026-04-01T13:30:00", "2026-04-01T13:35:00")):
    return csv([f"{t},100,101,99,100.25,10" for t in times])


@pytest.mark.parametrize("row", [
    "2026-04-01T13:31:00,100,101,99,100,10",  # off-grid
    "2026-04-01T13:30:00,100,99,98,100,10",  # invalid high
    "2026-04-01T13:30:00,100,101,99,100,-1",  # negative volume
    "2026-04-01T13:30:00,100,101,99,100,1.5",  # fractional contracts
    "2026-04-01T13:30:00,100,101,99,100.1,10",  # tick size
    "2026-04-01T13:30:00,100,inf,99,100,10",
    "2026-04-01T13:30:00,100,101,99,,10",
    ",100,101,99,100,10",
])
def test_reject_bad_market_data(row):
    with pytest.raises(p.DataError):
        p.read_bars(csv([row]), "test")


def test_duplicates_fail_instead_of_silently_choosing():
    with pytest.raises(p.DataError, match="duplicate"):
        p.read_bars(bars(("2026-04-01T13:30:00",) * 2), "test")


def test_alignment_does_not_fill_missing_es_or_future_rows():
    nq = p.read_bars(bars(), "NQ")
    es = p.read_bars(bars(("2026-04-01T13:35:00",)), "ES")
    result = p.align(nq, es)
    assert list(result.index) == [pd.Timestamp("2026-04-01T13:35:00Z")]
    assert not result.isna().any().any()


def test_dst_and_available_time():
    frame = p.read_bars(bars(("2026-03-06T14:30:00", "2026-03-09T13:30:00")), "NQ")
    result = p.decorate(frame)
    assert result.exchange_datetime.dt.hour.tolist() == [9, 9]
    assert result.exchange_datetime.dt.minute.tolist() == [30, 30]
    assert result.rth_clock.all()
    assert (result.available_at - result.index == p.BAR).all()
    evening = p.decorate(p.read_bars(bars(("2026-03-09T22:00:00",)), "NQ"))
    assert evening.session_date.iloc[0] == "2026-03-10"


def test_rolling_feed_keeps_older_rows_and_audits_corrections():
    old = p.read_bars(bars(), "old")
    new = p.read_bars(csv(["2026-04-01T13:35:00,100,102,99,101,12"]), "new")
    combined, report = p.merge_feed(old, new)
    assert len(combined) == 2
    assert combined.iloc[-1].close == 101
    assert combined.iloc[0].equals(old.iloc[0])
    assert report["conflicting_rows"] == 1


@pytest.fixture
def fake_feed(monkeypatch):
    payload = bars()
    monkeypatch.setattr(p, "pin", lambda *args: "a" * 40)
    monkeypatch.setattr(p, "api", lambda *args: {"tree": [
        {"path": f"{s}/{s}_5min_20260401_20260401.csv"} for s in p.SYMBOLS]})
    monkeypatch.setattr(p, "fetch", lambda url: payload)
    return {"schema_version": 1, "bar_minutes": 5, "feed_repository": "owner/feed",
            "feed_ref": "main", "series_policy": "test-root", "max_age_hours": 96,
            "older_sources": []}


def test_atomic_failure_keeps_previous_snapshot(tmp_path, monkeypatch, fake_feed):
    p.refresh(fake_feed, tmp_path, now="2026-04-01T14:00:00Z")
    pointer = (tmp_path / "CURRENT").read_text()
    monkeypatch.setattr(p, "fetch", lambda url: b"bad csv")
    with pytest.raises(p.DataError):
        p.refresh(fake_feed, tmp_path, now="2026-04-02T14:00:00Z")
    assert (tmp_path / "CURRENT").read_text() == pointer
    assert p.current_path(tmp_path).is_dir()


def test_staleness_recomputed_and_explicit_historical_access(tmp_path, monkeypatch, fake_feed):
    p.refresh(fake_feed, tmp_path, now="2026-04-01T14:00:00Z")
    monkeypatch.setattr(p, "utc_now", lambda: pd.Timestamp("2026-05-01T14:00:00Z"))
    with pytest.raises(p.DataError, match="Restricted"):
        p.load_dataset(tmp_path)
    with pytest.raises(p.DataError, match="Stale"):
        p.load_dataset(tmp_path, acknowledge_restrictions=True)
    result = p.load_dataset(tmp_path, historical=True, acknowledge_restrictions=True,
                            as_of="2026-04-01T13:35:00Z")
    assert len(result) == 1
    assert result.index[0] == pd.Timestamp("2026-04-01T13:30:00Z")
    assert result.attrs["snapshot_id"]


def test_corrupt_artifact_is_rejected(tmp_path, fake_feed):
    p.refresh(fake_feed, tmp_path, now="2026-04-01T14:00:00Z")
    (p.current_path(tmp_path) / "NQ_ES.parquet").write_bytes(b"corrupt")
    with pytest.raises(p.DataError, match="Corrupt"):
        p.load_dataset(tmp_path, historical=True, acknowledge_restrictions=True)


@pytest.mark.parametrize("approved,policy,conflict,included", [
    (False, "test-root", False, False),
    (True, "other-roll-method", False, False),
    (True, "test-root", True, False),
    (True, "test-root", False, True),
])
def test_history_requires_approval_compatible_policy_and_exact_overlap(
    tmp_path, monkeypatch, fake_feed, approved, policy, conflict, included
):
    fake_feed["older_sources"] = [{"id": "older", "symbol": "NQ", "repository": "owner/older",
        "ref": "main", "path": "older.csv", "approved": approved, "series_policy": policy}]
    history = bars(("2026-04-01T13:25:00", "2026-04-01T13:30:00"))
    if conflict:
        history = history.replace(b"100.25", b"100.5")
    monkeypatch.setattr(p, "fetch", lambda url: history if "older.csv" in url else bars())
    report = p.refresh(fake_feed, tmp_path, now="2026-04-01T14:00:00Z")
    assert (report["older_sources"][0]["disposition"] == "included") is included
    assert report["coverage"]["NQ"]["rows"] == (3 if included else 2)
    assert report["coverage"]["paired"]["rows"] == 2


def test_new_future_bars_do_not_change_past_observations():
    past = p.decorate(p.read_bars(bars(), "past"))
    extended = p.decorate(p.read_bars(bars(("2026-04-01T13:30:00", "2026-04-01T13:35:00",
                                          "2026-04-01T13:40:00")), "future"))
    pd.testing.assert_frame_equal(past, extended.loc[past.index])


def test_upstream_regression_and_policy_change_do_not_replace_current(tmp_path, monkeypatch, fake_feed):
    p.refresh(fake_feed, tmp_path, now="2026-04-01T14:00:00Z")
    pointer = (tmp_path / "CURRENT").read_text()
    monkeypatch.setattr(p, "fetch", lambda url: bars(("2026-04-01T13:30:00",)))
    with pytest.raises(p.DataError, match="regressed"):
        p.refresh(fake_feed, tmp_path, now="2026-04-02T14:00:00Z")
    assert (tmp_path / "CURRENT").read_text() == pointer
    fake_feed["series_policy"] = "changed"
    with pytest.raises(p.DataError, match="policy"):
        p.refresh(fake_feed, tmp_path, now="2026-04-02T14:00:00Z")
