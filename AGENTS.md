# AGENTS.md

## Project context

Project 53 is a Python research and backtesting platform for Nasdaq futures
(NQ/MNQ), with S&P 500 futures (ES/MES) as a comparison market.
Read README.md before starting work. Its architecture is planned, not implemented.
Do not describe planned capabilities as working features.

Guiding principle: Python measures. AI reasons. Backtesting decides what survives.

## Scope and priorities

- Build incrementally around the first milestone: load historical NQ and ES candles,
  synchronize observations, identify confirmed swings, detect bullish and bearish
  SMT divergence, and produce reproducible research output.
- Keep deterministic data processing, features, strategies, backtesting, risk,
  AI adapters, and reporting separate.
- Follow the proposed src/project53/ layout, with tests/ and docs/ alongside it.
- Use Python, Pandas/NumPy, and Pytest as appropriate. Keep AI integrations
  provider-independent.
- Do not introduce live brokerage execution during initial development.

## Research correctness

- Define data schemas, timestamp conventions, session boundaries, and missing-data
  handling explicitly. Preserve instrument identity and document futures contract
  selection or rollover assumptions when relevant.
- Synchronize NQ and ES using information available at the same evaluation time.
  Never silently fill missing observations with future data.
- Prevent lookahead bias. A swing requiring later candles becomes available only
  after confirmation; distinguish its pivot time from its confirmation time.
- Ensure signals and fills respect when information becomes available.
- Keep calculated facts separate from AI interpretations. AI must not fabricate
  measurements or substitute for deterministic feature calculations.
- Make research runs reproducible: record inputs, parameters, data coverage, and
  random seeds where used.
- For backtesting work, document commissions, slippage, position sizing, risk
  constraints, and execution assumptions. Keep out-of-sample evaluation separate
  from model or strategy selection.

## Development workflow

- Inspect existing code, configuration, applicable instructions, and the working
  tree before editing. Preserve user changes.
- Keep changes focused on the requested task and follow established conventions.
  Avoid unrelated refactors or unnecessary dependencies.
- Use the project's documented setup and check commands. Until tooling exists,
  do not claim that installation, tests, linting, or builds have passed.
- When adding tooling, document the exact setup and validation commands.
- Update documentation when interfaces, assumptions, behavior, or setup change.
- Never commit credentials, proprietary market data, or private strategy
  configurations to this public repository. Use synthetic fixtures for tests.

## Validation and handoff

- Add meaningful Pytest coverage for new calculations and bug fixes.
- Test timestamp alignment, missing observations, swing confirmation, and SMT
  edge cases when implementing those features.
- Include checks that future observations cannot change signals already available
  at an earlier evaluation time.
- Run relevant checks and report their actual results. State any checks that
  could not run and why.
- Summarize what changed, how it was validated, and remaining limitations.
