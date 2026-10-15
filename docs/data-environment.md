# Project 53 research data environment

The implemented data environment produces one versioned, synchronized **NQ/ES
five-minute dataset** for repeatable historical research. This is the first
implemented part of Project 53. Strategies and backtesting remain planned.

## Run locally

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
project53-data --root data
```

Use `project53-data --root data --require-fresh` for automated checks; exit code 2
means the dataset was built but current market data is stale. An upstream network
or validation error exits nonzero and leaves the previous snapshot in place.
The last attempt is recorded separately, so a failed refresh cannot masquerade as
a successful current refresh. Run the CLI on Linux/macOS (uses `fcntl` locking).

```python
from project53.data.pipeline import load_dataset

bars = load_dataset(
    "data", historical=True, acknowledge_restrictions=True,
    as_of="2026-04-01T14:00:00Z",
)
print(bars.attrs["snapshot_id"])
print(bars.attrs["quality"]["coverage"])
```

Every row has NQ and ES open/high/low/close/volume, per-instrument source SHA-256,
UTC bar-start index, `available_at` (bar start plus five minutes), New York
exchange datetime, Globex session date, and `rth_clock`. The loader only exposes
rows whose closing time is at or before `as_of`. Bar-level research must still
account for fill ambiguity, costs, slippage, and feature confirmation delays.

Default loader settings reject restricted or stale data. Historical access is
an explicit choice. It does not establish that a dataset is production-approved.

## Sources and compatible history

Source configuration: `config/data-sources.json`. Fetch directly from the pinned
commit of `axb0306/cme-futures-ohlc`, the upstream for our
`bbottorff-max/cme-futures-ohlc` mirror. This removes dependence on when the mirror
sync happens to run. No upstream Python is executed and no TopstepX API key is
needed. NQ and ES are the E-mini roots; MNQ and MES are not substituted.

Only native five-minute bars are used, preserving one resolution. Other upstream
resolutions and ticks are not mixed into the canonical dataset. Raw bytes are
archived under SHA-256 filenames, with repository, commit and exact file URL.
Downloads discover renamed files at that commit, rather than assuming yesterday's
filename still exists.

Each refresh retains previously downloaded history if upstream windows shrink
at the beginning, refuses regressions at the latest timestamp, and audits changed
overlap rows. New pinned feed bars win corrections. Every previous snapshot is
immutable; a test should record its snapshot ID to reproduce a historical run.
Raw archives and snapshots live outside public Git. Archive storage grows with
updates; back up the private state before applying any retention policy.

Older GitHub NQ source:
`bbottorff-max/nq-strategy-b-bot/data/nq_databento_5min.csv`.
It is downloaded into the raw archive and compared automatically, but remains
quarantined according to the existing
[Data Steward review](data-stewards/nq-databento-5min-intake.md).
Matching column names and timestamps are insufficient to combine contracts.

A history candidate is included only when `approved` is explicitly true,
`series_policy` agrees with the canonical feed, there is actual overlapping
data, and **all overlapping OHLCV values agree**. Approval must follow contract,
roll/adjustment, calendar and source-rights review. Do not simply toggle the flag
to bypass review. Incompatible files remain archived with a comparison report.
Changing source configuration requires a new data root, preventing previously
approved history from silently surviving a later policy change.

No older matching ES history has been located. NQ-only history never creates
synthetic ES bars or extends the synchronized dataset's range.

## Validation and outputs

Validation rejects malformed schema, empty inputs, duplicate/missing timestamps,
off-grid timestamps, partial/future bars, non-finite values, impossible OHLC,
nonpositive prices, prices outside the 0.25 tick grid, and negative/fractional
volume. The synchronized dataset uses an exact timestamp inner join, with no
forward-fill. Missing observations are counted; gaps are listed for review.

`data/CURRENT` atomically points to a completed directory in `data/snapshots/`:

- `NQ.parquet`, `ES.parquet`: retained validated instrument history and lineage.
- `NQ_ES.parquet`: the common dataset consumed by research.
- `manifest.json`: source commits, checksums, configuration, transform/code
  version, parent snapshot and hashes of every output.
- `quality.json`: coverage, freshness, excluded rows, corrections, history status.
- `gaps.json`: absent five-minute intervals requiring calendar reconciliation.

The loader verifies output hashes and recomputes freshness at read time.
Freshness allows at most 96 hours since the last closed bar, to tolerate normal
weekends; this is a coarse watchdog, not a holiday-aware service-level guarantee.
`rth_clock` is 09:30–16:00 New York on weekdays; holidays and early closes still
require a proper exchange-calendar audit. Session date rolls at 18:00 New York.
The source is root-labeled, with unverified contract-by-date/adjustment mapping
and upstream code containing fixed contract IDs. These limitations are explicit
in every quality report. The system does not claim production approval.

## Daily operations

The private `bbottorff-max/project-53-operations` repository runs the refresh
daily at **01:15 UTC** (20:15 previous day Central during daylight saving time;
19:15 in standard time), after the source's advertised 23:30 UTC update.
GitHub scheduled jobs can run late. Operations checks out a pinned Project 53
code commit, tests it, restores the last private data-state artifact, refreshes,
and uploads the resulting state and quality summary even if freshness fails.
Artifacts are retained for 90 days; each contains the accumulated state.
No market-data files are committed to the public repository.

A stale upstream intentionally makes the refresh job fail after retaining a
usable historical snapshot. This exposes the actual feed problem instead of
reporting that a successful download means current data.

For a local copy of the most recent state, get the run ID from private Actions:

```bash
gh run list --repo bbottorff-max/project-53-operations --workflow refresh-data.yml
gh run download RUN_ID --repo bbottorff-max/project-53-operations \
  --name project53-data-state --dir data
```

Use a new destination directory when downloading a snapshot, and retain existing
research snapshots. A fresh download can then be loaded with the Python example
above. Run the workflow manually with `gh workflow run refresh-data.yml --repo
bbottorff-max/project-53-operations`. Workflow updates must also advance its
pinned Project 53 code SHA when deploying code changes.

## Verified initial coverage

The initial live-source build on October 15, 2026 contained **16,786 paired bars**,
from **2026-01-20 15:55 UTC through 2026-04-15 23:25 UTC**. NQ had one additional
unpaired row. The older NQ source starts January 2, 2023 and ends May 7, 2026,
but conflicts in the overlap and does not supply older ES. The source feed is
stale as of this build; daily automation cannot manufacture missing market data.
