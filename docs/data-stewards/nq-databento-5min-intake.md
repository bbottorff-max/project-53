# Data Steward Review: NQ Databento 5-Minute Dataset

**Status:** QUARANTINED — review required; not approved for production backtests  
**Intake date:** 2026-10-13  
**Source:** https://raw.githubusercontent.com/prashanthaitha24/nq-strategy-b-bot/refs/heads/main/data/nq_databento_5min.csv  
**Local submitted artifact:** nq_databento_5min.xlsx (do not commit to public repository)  
**SHA-256 of submitted XLSX:** `99fe46f5c4871fb86bc3fb1ee8703715919a1f7b821c7c2bc4c76ed2ef8ba72a`

## Verified intake findings

- 235,667 OHLCV observations, 6 columns: datetime, open, high, low, close, volume.
- UTC timestamp coverage: 2023-01-02 23:00 to 2026-05-07 23:55.
- Yearly rows: 2023 70,323; 2024 70,635; 2025 70,126; 2026 24,583.
- 0 duplicate timestamps, 0 missing cells, 0 invalid OHLC ordering or negative volume.
- 920 intervals differ from five minutes; 50 ten-minute intervals and 4 fifteen-minute intervals require classification. Most longer intervals may reflect market closures; verify against CME calendar.
- Dataset is 5-minute bars, not tick-level or order-book data.

## Data Steward acceptance gates

1. **Licensing and provenance:** verify original Databento entitlement, schema, dataset identifier, and rights to download, reuse, or redistribute. Do not publish the dataset or treat the upstream GitHub file as proof of rights.
2. **Instrument mapping:** establish NQ symbol/contract identity by date, expiry, continuous-series stitching and back-adjustment methodology; flag rollover jumps and volume discontinuities.
3. **Calendar:** reconcile every irregular timestamp interval with CME Globex schedule, US holidays, early closes, DST, and data outages; produce missing-bar report.
4. **Price and volume:** compare sampled bars with independently sourced contract-specific data; validate tick size 0.25, volume units, extreme returns, and suspicious zero/low volume.
5. **Temporal standardization:** retain raw UTC, derive America/New_York session timestamps with DST-aware conversion, define RTH/ETH and 09:30 ET open.
6. **Reproducibility:** preserve original checksum, provenance URL, transformations, and quality report; version curated data outside public Git.
7. **Backtest approval:** after acceptance, document slippage, commissions, bar-level fill ambiguity, out-of-sample windows and leakage checks. Do not infer profitability from structural validity.

## Recommended steward outputs

- `data_quality_report.json` with findings and exceptions.
- `contract_rollover_audit.csv` and `missing_bars.csv`.
- `dataset_manifest.yaml` with license status, checksum, timestamps, symbols, and transform lineage.
- Written disposition: APPROVED FOR RESEARCH / RESTRICTED / REJECTED.

## Routing

**Owner:** Project 53 Data Stewards  
**Next action:** review the source and submitted workbook, verify licensing, and issue a disposition.  
**Dependency:** approval required before Strategy B benchmarking or production ingestion.

This intake document does not itself launch agents or transfer the workbook into their runtime.