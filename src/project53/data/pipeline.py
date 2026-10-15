"""Fetch pinned GitHub files and publish an atomic, auditable research snapshot."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd

FIELDS = ["open", "high", "low", "close", "volume"]
SYMBOLS = ("NQ", "ES")
BAR = pd.Timedelta(minutes=5)
TRANSFORM_VERSION = 1


class DataError(ValueError):
    """Input cannot safely be used for research."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def utc_now():
    return pd.Timestamp.now(tz="UTC")


def fetch(url: str) -> bytes:
    headers = {"User-Agent": "project53-data", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers=headers), timeout=90) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def api(repository, resource):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise DataError("Invalid GitHub repository")
    return json.loads(fetch(f"https://api.github.com/repos/{repository}/{resource}"))


def pin(repository, ref):
    commit = api(repository, f"commits/{quote(ref, safe='')}")
    return commit["sha"]


def read_bars(data: bytes, label: str) -> pd.DataFrame:
    frame = pd.read_csv(io.BytesIO(data))
    if list(frame.columns) != ["datetime", *FIELDS] or frame.empty:
        raise DataError(f"{label}: expected nonempty datetime/OHLCV schema")
    # Upstream explicitly documents naive timestamps as UTC, not local exchange time.
    try:
        frame["datetime"] = pd.to_datetime(frame.datetime, utc=True, format="mixed", errors="raise")
        for field in FIELDS:
            frame[field] = pd.to_numeric(frame[field], errors="raise")
    except (ValueError, TypeError) as error:
        raise DataError(f"{label}: invalid timestamp or numeric value") from error
    frame = frame.set_index("datetime").sort_index()
    validate(frame, label)
    return frame


def validate(frame, label):
    import numpy as np

    if frame.empty or frame.index.hasnans or frame.index.duplicated().any():
        raise DataError(f"{label}: empty data or duplicate/missing timestamps")
    if not isinstance(frame.index, pd.DatetimeIndex) or str(frame.index.tz) != "UTC":
        raise DataError(f"{label}: UTC timestamp index required")
    if (frame.index.asi8 % BAR.value != 0).any():
        raise DataError(f"{label}: timestamp not aligned to five minutes")
    values = frame[FIELDS].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise DataError(f"{label}: non-finite OHLCV")
    prices = frame[FIELDS[:4]]
    bad = (
        (prices <= 0).any(axis=1)
        | (frame.high < prices.max(axis=1))
        | (frame.low > prices.min(axis=1))
        | (frame.volume < 0)
        | (frame.volume % 1 != 0)
        | ((prices * 4).round(8) % 1 != 0).any(axis=1)
    )
    if bad.any():
        raise DataError(f"{label}: {int(bad.sum())} bars violate OHLC/tick/volume rules")


def compare(left, right):
    shared = left.index.intersection(right.index)
    if shared.empty:
        return {"overlap_rows": 0, "conflicting_rows": 0, "max_close_difference": None}
    a, b = left.loc[shared, FIELDS], right.loc[shared, FIELDS]
    return {
        "overlap_rows": len(shared),
        "conflicting_rows": int(a.ne(b).any(axis=1).sum()),
        "max_close_difference": float((a.close - b.close).abs().max()),
    }


def merge_feed(previous, incoming):
    """Retain rolling-window history; newest pinned feed wins audited corrections."""
    audit = compare(previous, incoming) if previous is not None else None
    if previous is None:
        return incoming, audit
    result = pd.concat([previous, incoming])
    return result[~result.index.duplicated(keep="last")].sort_index(), audit


def align(nq, es):
    """Exact-time inner join. Never create or forward-fill an observation."""
    return nq[FIELDS].add_prefix("nq_").join(es[FIELDS].add_prefix("es_"), how="inner")


def decorate(frame):
    frame = frame.copy()
    frame["available_at"] = frame.index + BAR
    local = frame.index.tz_convert("America/New_York")
    frame["exchange_datetime"] = local
    # Globex session date rolls at 18:00 New York, using wall time across DST.
    wall = local.tz_localize(None)
    frame["session_date"] = (wall + pd.Timedelta(hours=6)).strftime("%Y-%m-%d")
    frame["rth_clock"] = (local.dayofweek < 5) & (local.hour * 60 + local.minute >= 570) & (
        local.hour * 60 + local.minute < 960
    )
    return frame


def gap_report(frame):
    rows = []
    for before, after in zip(frame.index[:-1], frame.index[1:]):
        if after - before > BAR:
            rows.append({"before": before.isoformat(), "after": after.isoformat(),
                         "absent_intervals": int((after - before) / BAR) - 1,
                         "classification": "unclassified_closure_or_missing_data"})
    return rows


def summary(frame, now):
    return {"rows": len(frame), "first_bar_utc": frame.index.min().isoformat(),
            "last_bar_utc": frame.index.max().isoformat(),
            "age_hours": round(float((now - (frame.index.max() + BAR)) / pd.Timedelta(hours=1)), 3)}


def current_path(root):
    pointer = root / "CURRENT"
    if not pointer.exists():
        return None
    name = pointer.read_text().strip()
    if not re.fullmatch(r"[a-f0-9]{64}", name):
        raise DataError("Invalid CURRENT snapshot pointer")
    path = root / "snapshots" / name
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest["snapshot_id"] != name:
        raise DataError("Snapshot ID mismatch")
    for filename, checksum in manifest["outputs"].items():
        if Path(filename).name != filename or digest((path / filename).read_bytes()) != checksum:
            raise DataError(f"Corrupt snapshot output: {filename}")
    return path


def archive(root, repository, sha, path):
    url = f"https://raw.githubusercontent.com/{repository}/{sha}/{quote(path, safe='/')}"
    data = fetch(url)
    checksum = digest(data)
    destination = root / "raw" / f"{checksum}.csv"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and digest(destination.read_bytes()) != checksum:
        raise DataError("Raw archive checksum mismatch")
    destination.write_bytes(data)
    return data, {"repository": repository, "commit": sha, "path": path,
                  "sha256": checksum, "url": url}


def refresh(config, root, now=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    now = utc_now() if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        raise DataError("Refresh time must be timezone-aware")
    now = now.tz_convert("UTC")
    if config.get("schema_version") != 1 or config.get("bar_minutes") != 5:
        raise DataError("Only schema v1 and five-minute bars are supported")
    old_path = current_path(root)
    old_manifest = json.loads((old_path / "manifest.json").read_text()) if old_path else None
    if old_manifest and old_manifest["series_policy"] != config["series_policy"]:
        raise DataError("Series policy changed; rebuild in a separate data root")
    if old_manifest and old_manifest["config"] != config:
        raise DataError("Source configuration changed; rebuild in a separate data root")
    repository = config["feed_repository"]
    sha = pin(repository, config["feed_ref"])
    listing = api(repository, f"git/trees/{sha}?recursive=1")
    if listing.get("truncated"):
        raise DataError("Incomplete GitHub tree listing")
    sources, frames, feed_frames, corrections = [], {}, {}, {}
    for symbol in SYMBOLS:
        pattern = rf"{symbol}/{symbol}_5min_\d{{8}}_\d{{8}}\.csv"
        matches = [item["path"] for item in listing["tree"] if re.fullmatch(pattern, item["path"])]
        if len(matches) != 1:
            raise DataError(f"{symbol}: expected exactly one five-minute feed file, found {len(matches)}")
        data, provenance = archive(root, repository, sha, matches[0])
        incoming = read_bars(data, symbol)
        if (incoming.index + BAR > now).any():
            raise DataError(f"{symbol}: incomplete or future bar")
        incoming["source_sha256"] = provenance["sha256"]
        sources.append(provenance)
        previous = pd.read_parquet(old_path / f"{symbol}.parquet") if old_path else None
        if previous is not None:
            validate(previous, f"cached {symbol}")
            if incoming.index.max() < previous.index.max():
                raise DataError(f"{symbol}: upstream coverage regressed")
        frames[symbol], corrections[symbol] = merge_feed(previous, incoming)
        feed_frames[symbol] = incoming
    older_reports = []
    for candidate in config.get("older_sources", []):
        item = dict(candidate)
        try:
            old_sha = pin(item["repository"], item["ref"])
            data, provenance = archive(root, item["repository"], old_sha, item["path"])
            sources.append(provenance)
            history = read_bars(data, item["id"])
            if (history.index + BAR > now).any():
                raise DataError("History contains incomplete or future bars")
            item.update(compare(frames[item["symbol"]], history))
            item["coverage"] = summary(history, now)
            item["source"] = provenance
            eligible = (item.get("approved") is True
                        and item.get("series_policy") == config["series_policy"]
                        and item["overlap_rows"] > 0 and item["conflicting_rows"] == 0)
            item["disposition"] = "included" if eligible else "quarantined"
            if eligible:
                history["source_sha256"] = provenance["sha256"]
                frames[item["symbol"]], _ = merge_feed(history, frames[item["symbol"]])
        except (DataError, HTTPError, URLError, TimeoutError) as error:
            item.update(disposition="quarantined", error=str(error))
        older_reports.append(item)
    # Rows inherited from a prior snapshot must still have provenance.
    for symbol, frame in frames.items():
        validate(frame, symbol)
        if frame.source_sha256.isna().any():
            raise DataError("Missing per-row source lineage")
    paired = align(frames["NQ"], frames["ES"])
    if paired.empty:
        raise DataError("No synchronized NQ/ES observations")
    paired["nq_source_sha256"] = frames["NQ"].loc[paired.index, "source_sha256"]
    paired["es_source_sha256"] = frames["ES"].loc[paired.index, "source_sha256"]
    coverage = {symbol: summary(frame, now) for symbol, frame in frames.items()}
    stale = any(summary(frame, now)["age_hours"] > config["max_age_hours"] for frame in feed_frames.values())
    coverage["paired"] = summary(paired, now)
    report = {"checked_at": now.isoformat(), "status": "stale" if stale else "restricted_research",
              "fresh": not stale, "production_ready": False, "coverage": coverage,
              "unpaired_rows": {s: len(frames[s]) - len(paired) for s in SYMBOLS},
              "feed_corrections": corrections, "older_sources": older_reports,
              "limitations": ["Contract-by-date and rollover/back-adjustment mapping is unverified.",
                              "Source entitlement/reuse terms require review before production use.",
                              "Gap intervals are reported; CME holiday/early-close reconciliation is pending.",
                              "RTH is a clock label, not an exchange holiday calendar.",
                              "Bar-start semantics follow upstream History bars; available_at adds five minutes."]}
    prior_sources = old_manifest["sources"] if old_manifest else []
    all_sources = {s["sha256"]: s for s in [*prior_sources, *sources]}
    identity = {"transform_version": TRANSFORM_VERSION, "config": config,
                "code_commit": os.environ.get("PROJECT53_CODE_COMMIT", "local-development"),
                "sources": sorted(all_sources.values(), key=lambda s: s["sha256"]),
                "parent_snapshot": old_manifest["snapshot_id"] if old_manifest else None,
                "checked_at": now.isoformat()}
    snapshot_id = digest(json_bytes(identity))
    snapshots = root / "snapshots"
    snapshots.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".building-", dir=snapshots))
    try:
        for symbol, frame in frames.items():
            decorate(frame).to_parquet(stage / f"{symbol}.parquet")
        decorate(paired).to_parquet(stage / "NQ_ES.parquet")
        (stage / "quality.json").write_bytes(json_bytes(report))
        (stage / "gaps.json").write_bytes(json_bytes({s: gap_report(f) for s, f in frames.items()}))
        outputs = {p.name: digest(p.read_bytes()) for p in stage.iterdir()}
        manifest = {**identity, "snapshot_id": snapshot_id, "schema_version": 1,
                    "series_policy": config["series_policy"], "outputs": outputs,
                    "max_age_hours": config["max_age_hours"], "bar_minutes": 5,
                    "code_commit": os.environ.get("PROJECT53_CODE_COMMIT", "local-development")}
        (stage / "manifest.json").write_bytes(json_bytes(manifest))
        stage.rename(snapshots / snapshot_id)
        temporary_pointer = root / ".CURRENT.tmp"
        temporary_pointer.write_text(snapshot_id + "\n")
        temporary_pointer.replace(root / "CURRENT")
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    (root / "last_attempt.json").write_bytes(json_bytes({"ok": True, "snapshot_id": snapshot_id,
                                                         "checked_at": now.isoformat(), "fresh": not stale}))
    return report


def load_dataset(root="data", *, historical=False, acknowledge_restrictions=False, as_of=None):
    """Read one verified snapshot; default refuses stale or restricted data."""
    root = Path(root)
    path = current_path(root)
    if path is None:
        raise DataError("No completed snapshot. Run project53-data first.")
    manifest = json.loads((path / "manifest.json").read_text())
    report = json.loads((path / "quality.json").read_text())
    if not acknowledge_restrictions and not report["production_ready"]:
        raise DataError("Restricted research data: acknowledge_restrictions=True is required")
    if not historical:
        attempt = json.loads((root / "last_attempt.json").read_text())
        if not attempt["ok"]:
            raise DataError("Latest refresh failed; use historical=True only for historical research")
        for symbol in SYMBOLS:
            last = pd.Timestamp(report["coverage"][symbol]["last_bar_utc"]) + BAR
            if (utc_now() - last) / pd.Timedelta(hours=1) > manifest["max_age_hours"]:
                raise DataError("Stale data: historical=True is required")
    frame = pd.read_parquet(path / "NQ_ES.parquet")
    if as_of is not None:
        cutoff = pd.Timestamp(as_of)
        if cutoff.tzinfo is None:
            raise DataError("as_of must include a timezone")
        frame = frame.loc[frame.available_at <= cutoff]
    frame.attrs.update(snapshot_id=manifest["snapshot_id"], quality=report)
    return frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/data-sources.json")
    parser.add_argument("--root", default="data")
    parser.add_argument("--require-fresh", action="store_true")
    args = parser.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    # Serialize writers; readers see either the previous or the new CURRENT pointer.
    import fcntl
    with (root / ".refresh.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            report = refresh(json.loads(Path(args.config).read_text()), root)
        except Exception as error:
            (root / "last_attempt.json").write_bytes(json_bytes(
                {"ok": False, "checked_at": utc_now().isoformat(), "error": str(error)}))
            raise
    print(json.dumps({"status": report["status"], "coverage": report["coverage"],
                      "older_sources": [{"id": s["id"], "disposition": s["disposition"]}
                                        for s in report["older_sources"]]}, indent=2))
    if args.require_fresh and not report["fresh"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
