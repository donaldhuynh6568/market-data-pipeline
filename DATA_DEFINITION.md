# Data definition and collection logic

This document explains what the pipeline collects, how often it collects it, how the exported sheets are combined, and what "unusual activity" means in this project.

## 1) Price data

The price collector pulls daily OHLCV bars for the ticker universe in `tickers.yaml`.

### Frequency
- Default collection frequency: daily bars
- Lookback range: configurable with `--days` in the price collector
- Example: `uv run python -m market_data_pipeline.collectors.price --days 30`
- Intraday bars (1-hour, 4-hour) are also available via `--timeframe` — see
  section 2 below.

### What each row contains
Each row is a bar for a specific symbol and timestamp. Typical fields include:
- `symbol`
- `timestamp`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `asset_class` (`equity` or `crypto`)

### Why it matters
These rows are the baseline market data used to understand broad price movement and compare later option activity against the underlying asset.

### Future modifications
If you want more detail later, you could add:
- finer intraday intervals (e.g. 1-minute, 5-minute — 1-hour and 4-hour exist today)
- custom date windows
- asset-specific metadata
- alternate providers or granular bar intervals

## 2) Intraday price data, swing structure, and the equity volume-floor check

Built to close specific gaps: `reference/orderflow_strategy_playbook.md`'s
Environment step needs higher-timeframe (1H/4H) swing structure, which daily
bars can't provide; and checking whether a move has real participation
behind it needs 5-minute granularity. This section covers the intraday bars
themselves and the two transforms built on top of them.

### Intraday bars
Same collector as daily bars (`market_data_pipeline/collectors/price.py`),
selected with `--timeframe`:
- `--timeframe 1Hour` → `data/price_data_1h.parquet`
- `--timeframe 4Hour` → `data/price_data_4h.parquet`
- `--timeframe 5Min` → `data/price_data_5m.parquet`
- `--timeframe 1Day` (default) → `data/price_data.parquet`, unchanged from
  before — intraday runs never overwrite the daily dataset.

Confirmed during development: all three intraday timeframes work with the
same Alpaca credentials already used for daily bars — no paid SIP tier is
required for aggregated intraday bars (that requirement is specific to raw
tick/quote data, a separate and still-unbuilt gap — see
`reference/orderflow_pipeline_buildout_plan.md` §4-5).

### Swing structure
`market_data_pipeline/transforms/swing_structure.py` reads a
`price_data_{1h,4h}.parquet` file and writes
`data/swing_structure_{1h,4h}.parquet`, one row per symbol.

**Swing point definition:** a bar's `high` (or `low`) is a confirmed swing
point only if it's the strict extreme within `--window` bars (default 3) on
*both* sides. This means the most recent `window` bars can never be
confirmed yet — a swing can't be identified until price has moved away from
it. This is expected lag, not a bug.

**Structure classification** (compares the last 2 confirmed swing highs and
last 2 confirmed swing lows):
- `UPTREND` — higher high and higher low
- `DOWNTREND` — lower high and lower low
- `SIDEWAYS` — mixed (higher high but lower low, or vice versa)
- `UNKNOWN` — fewer than 2 swing highs or lows confirmed yet. This is a
  "not enough history" state, not an actual sideways read — treat it as
  "no opinion yet," not as a genuine flat market. Confirmed live during
  development: `SHV` (a low-volatility short-term Treasury ETF) returned
  `UNKNOWN` on the 4H timeframe simply because it doesn't move enough to
  produce two confirmed swings in a 30-day window.

Output fields: `symbol`, `structure`, and the actual `last_swing_high` /
`prior_swing_high` / `last_swing_low` / `prior_swing_low` values and
timestamps used to derive it — the classification is always auditable back
to the specific bars that produced it, not a black box.

### Equity volume-floor check
`market_data_pipeline/transforms/volume_floor.py` reads
`data/price_data_5m.parquet` and writes `data/volume_floor_5m.parquet`.
Same underlying idea as the futures volume-floor check (§11) — "does this
move have real participation behind it" — but **calibrated to each symbol's
own trailing history instead of one borrowed fixed number**. A 20,000-
contract floor means something for MNQ; it means nothing for a stock like
ASML, whose typical 5-minute volume is a small fraction of that. Defaults to
the `large_cap` tickers (override with `--symbols`).

**Baseline calculation:** for each bar, `baseline_volume` is the trailing
rolling mean of the prior `--window` bars (default 20, ≈100 minutes) for
that same symbol — computed with `shift(1)` before the rolling mean so the
current bar never contributes to its own baseline (a real spike would
otherwise partially absorb itself into the number it's being compared
against). `NaN` until a symbol has accumulated `window` bars of history —
expected, not a bug, same "not enough data yet" pattern used elsewhere in
this pipeline (e.g. swing structure's `UNKNOWN`).

**Flag:** `volume_ratio = volume / baseline_volume`;
`volume_floor_met = volume_ratio >= --multiplier` (default 2.0 — a bar needs
at least double its own symbol's recent average to be flagged). Both the
window and multiplier are tunable starting points, not fixed truths — same
spirit as the `unusual_activity` threshold in the options collector below.

Confirmed during development across the `large_cap` universe: baseline
volumes ranged from ~7,500 (ASML) to ~665,000 (NVDA) — roughly a 90x spread
— which is exactly why a single shared threshold wouldn't work here the way
it does for the three futures contracts in §11.

## 3) Options data

The options collector filters the option chain around each equity and pulls data for active contracts with the highest signal value.

### Frequency
- Snapshot-based collection, not a full historical options time series
- The project pulls a daily snapshot of option contracts near the money and near expiry
- The script filters by:
  - moneyness (default ±20% from spot)
  - maximum expiry window (default 60 days)
- Configurable with:
  - `--moneyness`
  - `--max-expiry-days`

### Contract filtering logic
The project intentionally does not collect every option contract. It focuses on the area most likely to show meaningful flow:
- strikes close to the current stock price
- expirations near-term
- active contracts only
- contracts with zero volume and zero open interest are removed as dead strikes

### Key option fields
Each row includes:
- `contract_symbol`
- `underlying_symbol`
- `strike_price`
- `expiration_date`
- `type` (`call` or `put`)
- `open_interest`
- `volume`
- `implied_volatility`
- `delta`
- `spot_price`
- `snapshot_date`

## 4) CFTC COT positioning data

The COT collector is intended as a complementary data source for metals focused on larger positioning trends rather than single-contract option flow.

### Frequency
- Typically weekly or report-based, depending on the source file you provide
- The collector can ingest either a local CSV or ZIP export or a remote URL via `--source-url`

### Why this matters for GLD/SLV
CFTC reports break positions into categories such as:
- Producer/Merchant
- Swap Dealers
- Managed Money
- Other Reportables

This is valuable because `Managed Money` is much closer to the question of "what are large speculators doing?" than a simple ETF put/call ratio.

### Expected output
The collector normalizes the raw source into a consistent table and filters for relevant metals names such as `Gold` and `Silver`.

### Derived positioning metrics
The collector adds large-speculator positioning fields on top of the raw report:
- `spec_category` — which source category was used: `managed_money` (from the
  CFTC Disaggregated report) if present, otherwise `noncommercial` (from the
  Legacy report) as a fallback. These are not equivalent groupings, so the
  category actually used is always recorded rather than assumed.
- `spec_long`, `spec_short`, `spec_net` (`spec_long - spec_short`)
- `spec_net_change_1w` — change in `spec_net` since the prior report for that
  commodity
- `spec_net_percentile` — percentile rank of `spec_net` within the report
  history currently on hand (a single-report pull yields `NaN`, which is
  expected — percentile only becomes meaningful once multiple reports have
  accumulated)

### Observation date vs. release date
To avoid look-ahead bias in any future backtest, the collector also adds:
- `observation_date` — the date the positions reflect (the report's "as of"
  Tuesday)
- `release_date` — an approximation of when the report was actually published
  and available to the market, computed as `observation_date + 3 days`
  (Tuesday positions → Friday release). This is a fixed offset, not a full
  CFTC holiday calendar, so treat it as an approximation. A backtest should
  never use a row before its `release_date`.

### Future tuning
If you add more metals or contract groups later, the important things to keep configurable are:
- the target commodity filter list
- the source URL or local file path
- the normalized field names and category mapping

## 5) Gamma exposure (GEX)

The GEX transform estimates dealer gamma positioning from the collected
options chain's Greeks. It reads `data/options_data.parquet` and writes
`data/gex_data.parquet`.

### Calculation
For each contract:
`gamma_exposure = sign * gamma * open_interest * 100 * spot_price^2 * 0.01`

where `sign` is `+1` for calls and `-1` for puts. This assumes dealers are net
long call gamma and net short put gamma — the standard retail approximation
used when the actual dealer book isn't observable. **GEX here is an estimate
of market structure, not a direct observation of dealer positioning.**

### Output fields
Per `symbol`/`date`:
- `total_gamma`, `call_gamma`, `put_gamma`
- `max_positive_gamma_strike`, `max_negative_gamma_strike` — candidate gamma
  walls
- `gamma_flip` — the strike where cumulative gamma (summed low-to-high strike)
  crosses zero, linearly interpolated between the bracketing strikes
- `gamma_regime` — `POSITIVE_GAMMA`, `NEGATIVE_GAMMA`, or `NEAR_FLIP` (when
  spot is within 1% of `gamma_flip`)

### Requirement
The options collector must have captured `gamma` (added alongside the
existing `delta`/`implied_volatility` snapshot fields) — if `gamma` is
missing or entirely null, the transform stops with a clear error telling you
to re-run the options collector first.

## 6) FRED macroeconomic data

The FRED collector pulls a curated set of series from the Federal Reserve
Economic Data API and writes `data/fred_data.parquet`.

### Series collected
Federal Reserve (`FEDFUNDS`, `DFF`, `SOFR`), Treasury yields (`DGS2`, `DGS5`,
`DGS10`, `DGS30`), inflation (`CPIAUCSL`, `CPILFESL`, `PCEPI`, `PCEPILFE`),
employment (`UNRATE`, `PAYEMS`, `ICSA`), growth (`GDP`, `GDPC1`, `INDPRO`,
`RSAFS`), liquidity (`WALCL`, `M2SL`, `RRPONTSYD`, `WTREGEN`), dollar
(`DTWEXBGS`), and volatility (`VIXCLS`) — see `DEFAULT_SERIES` in
`market_data_pipeline/collectors/fred.py`, overridable with `--series`.

### Output fields
`series_id`, `date`, `value`. FRED encodes missing observations as `"."` in
its API response; the collector converts these to `NaN`.

### Known limitation: no revision/vintage tracking
FRED series are frequently revised after initial release (e.g. GDP, payrolls).
This collector stores only the latest value FRED currently reports for each
observation date — it does **not** track the ALFRED real-time/vintage history
of what value was known on any given past date. This means using
`fred_data.parquet` naively in a historical backtest can leak look-ahead bias
in the same way plan.md warns about for CPI/employment data. Building proper
vintage tracking (via FRED's `realtime_start`/`realtime_end` parameters or the
ALFRED API) is a real future improvement, not implemented here.

## 7) SEC EDGAR filings

The SEC collector looks up each configured equity ticker's CIK and pulls
recent filing metadata, writing `data/sec_filings.parquet`.

### Scope
Actuals only — filing metadata, not fundamentals extraction and not
earnings-surprise calculation. Earnings surprise needs analyst *estimates* as
well as actuals, and there's no free estimates source in the current stack.

### Default form types
`10-K`, `10-Q`, `8-K` (overridable with `--forms`).

### Output fields
`ticker`, `cik`, `form`, `observation_date`, `release_date`,
`accessionNumber`, `primaryDocument`. `observation_date`/`release_date` reuse
the vocabulary introduced for COT positioning data, and here they're exact
(not approximated): `observation_date` is the filing's `reportDate` (the
period it covers) and `release_date` is the actual SEC `filingDate` — the
date the document became public.

### Ticker universe caveat
Most of `tickers.yaml`'s original `equities` list is ETFs, which file
different SEC form types (N-CEN, N-PORT) than the 10-K/10-Q/8-K default here.
This collector is far more meaningful for the `large_cap` category of
individual stocks. Confirmed during development: some ETFs (e.g. `SPY`) do
resolve to a CIK and simply return no 10-K/10-Q/8-K matches, while several
others (`IWM`, `EFA`, `EEM`, `TLT`, `SHV`, the sector SPDRs) aren't in SEC's
ticker-to-CIK map at all and are skipped with a warning.

## 8) Senate trading disclosures (historical only)

The congress collector pulls from the community-maintained Senate Stock
Watcher dataset and writes `data/congress_trades.parquet`.

### Critical limitation: not a live feed
**This dataset only covers 2012-06-14 through 2020-12-02.** It was confirmed
stale during development — no free, structured, currently-updated source for
congressional trading disclosures was found. Use this for historical/pattern
research, not for monitoring current trades.

### Known gaps
- House disclosures aren't covered at all — the free House Stock Watcher
  source that used to serve this data is offline (confirmed 403 errors), and
  the official House Clerk site has no structured API.
- The official Senate source (`efdsearch.senate.gov`) has no API either; it's
  a gated search form. Scraping it directly was considered and explicitly
  deferred as too fragile for this pass.

### Output fields
`transaction_date`, `owner`, `ticker`, `asset_description`, `asset_type`,
`type` (e.g. `Purchase`, `Sale (Full)`), `amount` (a disclosed dollar range,
not an exact figure — that's how the underlying disclosures work), `comment`,
`senator`, `ptr_link`.

## 9) Corporate insider transactions (Form 4)

The insider collector pulls recent SEC Form 4 filings for each `large_cap`
ticker and writes `data/insider_trades.parquet`.

### Scope
Only `nonDerivativeTable` transactions (plain stock buys/sells) are parsed.
`derivativeTable` transactions (option grants, RSU vesting, and similar) are
explicitly out of scope for this pass.

### How it works
For each ticker: look up its CIK, list recent Form 4 filings via SEC's
company search, then for each filing fetch its document index and identify
the ownership XML document by its root tag (`ownershipDocument`) rather than
by filename — filenames aren't consistent across filings.

### Output fields
Issuer (`issuerCik`, `issuerTradingSymbol`), reporting owner (`rptOwnerName`,
officer/director/10%-owner flags, `officerTitle`), and per-transaction
`transactionDate`, `transactionCode`, `transactionShares`,
`transactionPricePerShare`, `transactionAcquiredDisposedCode`,
`sharesOwnedFollowingTransaction`.

### Performance note
This collector makes multiple SEC requests per filing found (list filings,
fetch its index, fetch its document) — expect a full run across all
`large_cap` tickers to take noticeably longer than the other collectors.

## 10) Institutional 13F holdings

The institutional collector pulls the latest Form 13F holdings for a curated
watchlist of well-known institutional investors and writes
`data/institutional_holdings.parquet`.

### Why a curated watchlist, not "who holds our tickers"
Finding every fund that holds a specific security would require reverse
full-text search across all 13F filers, which isn't practical for free. The
tractable free approach is the other direction: track specific, well-known
investors' full holdings.

### Default watchlist
Berkshire Hathaway, Bridgewater Associates, Scion Asset Management, Pershing
Square Capital Management, Renaissance Technologies — see `DEFAULT_WATCHLIST`
in `market_data_pipeline/collectors/institutional.py`, filterable with
`--only`.

### Scope
Only the *latest* 13F-HR filing per manager — no historical backfill. Filings
before 2013Q2 aren't guaranteed to be in the structured XML format this
collector parses.

### Output fields
`nameOfIssuer`, `titleOfClass`, `cusip`, `value` (reported in thousands of
dollars, per SEC convention), `shrsOrPrnAmt` (shares or principal amount),
`sshPrnamtType`, `investmentDiscretion`.

## 11) Futures 5-minute volume (volume-floor check)

The futures volume collector supports a specific rule from
`reference/orderflow_strategy_rules.yaml` — that a candle with fewer than
20,000 contracts traded is low-participation and its signal should be
discounted. Nothing else in this pipeline could check this: it needs
5-minute volume on the relevant futures contract, and Alpaca (this
pipeline's only other data provider) **has no futures data at all** —
confirmed by inspecting `alpaca-py`'s available client classes, which cover
only stocks, crypto, options, news, and corporate actions.

### Data source — read this before relying on it
This collector pulls from Yahoo Finance's public chart endpoint
(`query1.finance.yahoo.com/v8/finance/chart/{symbol}`), the same one the
`yfinance` package wraps. It is:
- **Unofficial** — not a published, supported API; no SLA; could change or
  start rate-limiting without notice
- **Delayed** — confirmed live during development at roughly 10 minutes
  behind real time, not true real-time
- **Limited to ~60 days of intraday history** — this is a "check recent
  participation" tool, not a historical dataset

This is a real, accepted tradeoff, not an oversight: Alpaca simply doesn't
offer an alternative, and this was the only free/keyless option found.

### Default tickers
`MNQ=F` (Micro E-mini Nasdaq-100, QQQ-linked), `MES=F` (Micro E-mini S&P
500, SPY-linked), `SIL=F` (Micro Silver, SLV-linked) — configured under a
new `futures` key in `tickers.yaml`, loaded via
`market_data_pipeline.tickers.load_futures_tickers()`.

### Output fields
`symbol`, `timestamp`, `open`, `high`, `low`, `close`, `volume`, and
`volume_floor_met` (`volume >= 20,000`, matching
`min_volume_per_5min_candle_contracts` in
`reference/orderflow_strategy_rules.yaml` exactly). The most recent bar
returned by the API is always still forming (reported with `volume == 0`)
and is dropped rather than reported as a false low-volume reading.

## 12) What “unusual activity” means

This project defines unusual activity as a contract or set of contracts showing unusually high volume relative to open interest.

### Calculation
The script computes:
- `vol_oi_ratio = volume / open_interest`

If `open_interest > 0` and `vol_oi_ratio > 1.0`, the script flags the contract as unusual.

### Interpretation
This means today's trading volume exceeds the current open interest. In simple terms, the market is seeing more activity than the existing position base suggests. That often points to:
- new positioning
- fresh interest in a contract
- a possible directional or sentiment signal

This is not a guaranteed directional prediction, but it is a practical signal for further review.

### Why this heuristic is useful
It is a lightweight, explainable signal that does not require a large data pipeline or complicated backtesting setup. It is meant to give an analyst a quick shortlist of contracts to review.

### Future tuning
If you want to evolve the definition later, you can adjust:
- threshold for unusual activity (`> 1.0` right now)
- which metrics are included in the final output
- additional logic such as comparing to a rolling average or prior days

## 13) What the combined workbook does

The combine step reads the local parquet files and writes a single Excel workbook named `market_data.xlsx`.

### Workbook structure
The workbook always includes:
- `Price Data` — all price rows
- `Options Data` — all filtered option rows
- `Unusual Activity` — only contracts flagged by the unusual activity logic

And adds these sheets when the corresponding parquet file exists:
- `COT Data` — normalized CFTC positioning rows with derived metrics
- `GEX Data` — per-symbol gamma exposure estimates
- `FRED Data` — macroeconomic series
- `SEC Filings` — 10-K/10-Q/8-K filing metadata
- `Congress Trades` — historical Senate trading disclosures (2012-2020 only)
- `Insider Trades` — Form 4 non-derivative transactions
- `13F Holdings` — latest institutional holdings for the curated watchlist
- `Price 1H` / `Price 4H` — intraday OHLCV bars
- `Swing Structure 1H` / `Swing Structure 4H` — per-symbol trend classification
- `Volume Floor 5m` — large-cap 5-minute bars with the per-symbol volume-floor flag
- `Futures Volume 5m` — 5-minute futures bars with the fixed volume-floor flag

### Why combine them
This gives a single review file that can be opened in Excel without manual joins or additional work. It is designed for quick analysis and manual review.

### Why the unusual activity sheet is important
The full options dataset can be large. The filtered unusual-activity sheet narrows the data to the rows most relevant for signs of fresh market positioning.

## 14) Data frequency summary

| Dataset | Frequency | Notes |
|---|---|---|
| Price data | Daily bars | Configurable with `--days` |
| Intraday price data | 1H/4H/5Min bars | `--timeframe`; separate files from the daily dataset |
| Swing structure | Derived on-demand from intraday bars | Not a separate collection frequency of its own |
| Equity volume-floor check | Derived on-demand from 5-minute bars | Per-symbol baseline, not a separate collection frequency |
| Options snapshot | Daily collection pass | Uses moneyness and expiry filters |
| COT positioning data | Weekly or report-based | Complementary positioning signal for metals markets |
| GEX | Derived on-demand from the latest options snapshot | Not a separate collection frequency of its own |
| FRED | Daily/weekly/monthly/quarterly, per series | Latest value only, no vintage tracking |
| SEC filings | Event-based | 10-K/10-Q/8-K as they're filed |
| Congress trades | Static historical snapshot | Source stops at Dec 2020, not updated |
| Insider trades (Form 4) | Event-based | Reflects whatever is currently on EDGAR |
| Institutional 13F holdings | Quarterly | Only the latest filing per manager is kept |
| Futures 5-minute volume | On-demand, ~10min delayed | Unofficial source, ~60-day intraday history limit |
| Combined workbook | On-demand after collection | Produces a review-friendly Excel file |

## 15) How this should evolve

This project is intentionally designed so these definitions can be changed without rewriting the whole pipeline.

If the logic changes later, the places most likely to be updated are:
- `tickers.yaml` for the symbol universe
- `market_data_pipeline/collectors/price.py` for price data frequency, timeframe choices, and fields
- `market_data_pipeline/transforms/swing_structure.py` for the swing-point window and classification logic
- `market_data_pipeline/transforms/volume_floor.py` for the baseline window and multiplier
- `market_data_pipeline/collectors/options.py` for contract filtering and unusual activity logic
- `market_data_pipeline/collectors/cot.py` for positioning category detection and derived metrics
- `market_data_pipeline/transforms/gex.py` for the gamma exposure formula and regime thresholds
- `market_data_pipeline/collectors/fred.py` for the macro series list
- `market_data_pipeline/collectors/sec.py` for filing form types and CIK lookup
- `market_data_pipeline/collectors/congress.py` for the congressional data source (currently historical-only)
- `market_data_pipeline/collectors/insider.py` for Form 4 parsing scope (currently non-derivative only)
- `market_data_pipeline/collectors/institutional.py` for the 13F watchlist
- `market_data_pipeline/collectors/futures_volume.py` for the futures ticker list and volume-floor threshold
- `market_data_pipeline/combine.py` for workbook structure and sheet output

The goal is to keep the business definitions clear and explicit, so future data collection changes are easy to reason about.
