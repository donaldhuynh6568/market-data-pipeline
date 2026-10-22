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
ASML, whose typical 5-minute volume is a small fraction of that. Covers every symbol in
the file by default, ETFs and crypto included (restrict with `--symbols`).

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
The collector normalizes the raw source into a consistent table and filters for relevant metals names such as `Gold` and `Silver`, plus crypto futures: CME `BITCOIN` and `ETHER CASH SETTLED`, and Coinbase Derivatives `NANO SOLANA` (the only CFTC-reportable SOL futures; small, so weaker as a positioning signal). Micro/Nano/perp-style variants of BTC/ETH are deliberately excluded.

Crypto markets appear in the CFTC **Legacy** report (Socrata dataset `6dca-aqww`), not the Disaggregated one, so their spec category is `noncommercial` rather than `managed_money`. Pass `--source-url` twice to combine both reports in one run.

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

## 14) Fundamentals / cash-flow data (SEC XBRL)

The fundamentals collector looks up each `large_cap` ticker's CIK and pulls
SEC's XBRL companyfacts API, writing `data/fundamentals_data.parquet`. A
transform (`market_data_pipeline/transforms/fundamentals_ratios.py`) then
derives ratios into `data/fundamentals_ratios.parquet`.

### Frequency
Event-based, like SEC filings: companyfacts always reflects the latest
10-K/10-Q filed for a company, with no fixed collection schedule of its own.

### Scope
`large_cap` only — the same reasoning as the SEC filings ticker universe
caveat above (§7): fundamentals ratios aren't meaningful for the ETF
categories in `tickers.yaml`.

### Output fields (`fundamentals_data.parquet`)
`ticker`, `cik`, `fiscal_period_end`, `form`, `revenue`, `net_income`,
`operating_income`, `interest_expense`, `depreciation_amortization`, `ocf`
(operating cash flow), `capex`, `cash`, `total_debt`, `shares_outstanding`,
plus a `*_tag_used` column per metric recording which XBRL tag was actually
matched.

### Output fields (`fundamentals_ratios.parquet`)
`ticker`, `fiscal_period_end`, `fcf` (OCF − CapEx), `fcf_margin`,
`cash_conversion_ratio` (OCF / net income — a quality-of-earnings check),
`capex_pct_revenue` and its trailing trend, `net_debt` (total debt − cash),
`net_debt_to_ebitda`, `interest_coverage` (operating income / interest
expense).

### Known limitation: XBRL tag inconsistency across filers
Different companies tag economically identical concepts under different XBRL
tags (e.g. `Revenues` vs. `RevenueFromContractWithCustomerExcludingAssessedTax`),
and companyfacts includes duplicate/overlapping period entries (a 10-Q can
report both quarterly and year-to-date cumulative cash-flow figures under the
same tag, and restatements happen). The collector uses a per-metric
tag-fallback list (see `TAG_FALLBACKS` in
`market_data_pipeline/collectors/fundamentals.py`) and prefers the
most-recently-filed entry for a duplicated period, recording which tag was
used per metric rather than silently guessing. EBIT/EBITDA are never
standard XBRL tags anywhere — they're always derived from
`OperatingIncomeLoss` (+ D&A for EBITDA), never looked up directly.

### Not yet implemented
Forward guidance and analyst consensus estimates are not present in XBRL and
are not collected here — see `intent/feature-fundamentals-collector.md` for
the planned (not yet built) follow-up phase, which would need a separate
third-party provider and a new API key. Valuation ratios that need market cap
(P/E, EV/EBITDA, EV/FCF, FCF yield) are also not yet built, though they only
need `shares_outstanding` from this dataset times price already in
`price_data.parquet` — no new provider required.

## 15) Data frequency summary

| Dataset | Frequency | Notes |
|---|---|---|
| Price data | Daily bars | `--days` sizes a first run or new ticker; later runs append only new bars (`--full` rebuilds) |
| Intraday price data | 1H/4H/5Min bars | `--timeframe`; separate files from the daily dataset; appended incrementally like daily |
| Swing structure | Derived on-demand from intraday bars | Not a separate collection frequency of its own |
| Equity volume-floor check | Derived on-demand from 5-minute bars | Per-symbol baseline, not a separate collection frequency |
| Options snapshot | Daily collection pass | Uses moneyness and expiry filters; each day is appended (last 90 days kept), readers use the latest snapshot |
| COT positioning data | Weekly or report-based | Complementary positioning signal for metals markets |
| GEX | Derived on-demand from the latest options snapshot | Not a separate collection frequency of its own |
| FRED | Daily/weekly/monthly/quarterly, per series | Latest value only, no vintage tracking |
| SEC filings | Event-based | 10-K/10-Q/8-K as they're filed |
| Congress trades | Static historical snapshot | Source stops at Dec 2020, not updated |
| Insider trades (Form 4) | Event-based | Reflects whatever is currently on EDGAR |
| Institutional 13F holdings | Quarterly | Only the latest filing per manager is kept |
| Futures 5-minute volume | On-demand, ~10min delayed | Unofficial source, ~60-day intraday history limit |
| Fundamentals / cash-flow (SEC XBRL) | Event-based | Reflects latest 10-K/10-Q filed; ratios derived on-demand |
| Combined workbook | On-demand after collection | Produces a review-friendly Excel file |

## 17) Momentum ranking

`market_data_pipeline/transforms/momentum.py` reads `price_data.parquet` and writes `momentum.parquet` (sheet `Momentum`), one row per symbol, ranked best first by `score`.

### Score and delta (the ranking signal)
Defined in the Methodology sheet of `reference/Rotation_Positions_Tracker.xlsx`:

- `momentum_10d_pct` = (close today / close 10 bars ago - 1) x 100
- `vol_10d_pct` = standard deviation of the last 10 daily returns x sqrt(periods per year) x 100. Simple returns, population standard deviation (ddof=0); 252 periods for equities, **365 for crypto** (crypto trades every day, so this differs slightly from a flat-252 calculation).
- `score` = `momentum_10d_pct` / `vol_10d_pct`, with volatility floored at 5% so near-constant prices can't blow the score up.
- `delta_5d` / `delta_10d` / `delta_20d` = today's score minus the score 5 / 10 / 20 bars ago.
- `delta_signal`: `ACCELERATING` (all three deltas positive), `DECELERATING` (all three negative), `MIXED`, or `UNKNOWN` (not enough history; needs about 31 bars).

Read the deltas before the score: a high score with negative deltas is a name that is already fading. Names in the `cash_like` list in `tickers.yaml` (currently `SHV`) keep their metrics but get no `rank` and are flagged `cash_like`, because their near-zero volatility inflates the score.

### Other columns (secondary, from reference/rotationstrategy.md)
- `ret_5d` / `ret_20d` / `ret_60d`: simple close-to-close returns over that many bars (trading days for equities, calendar days for crypto).
- `smoothed_ret_*`: the same windows on an EMA (span 5) of the median price `(high+low)/2`.
- `acceleration_20d`: latest 20-bar smoothed return minus the 20-bar return before it.
- `ann_vol` and `vol_adj_score`: 60-bar log-return volatility and the 60-bar smoothed return divided by it. No longer used for ranking.
- `above_200d_ma`: trend flag, only populated with at least 200 bars.

Fields are NaN when there is not enough history; collect with `--days 90` (or more) for the 60-bar metrics and about 300 days for the 200-day flag. `--as-of YYYY-MM-DD` recomputes as of an earlier date.

## 18) Crypto perpetual funding rates and open interest

`market_data_pipeline/collectors/perps.py` writes `perps_data.parquet` (sheet `Perps Data`) with `symbol`, `venue`, `timestamp`, `funding_rate`, `open_interest` (in base coin), `mark_price` and `open_interest_usd`. Venues: Binance and Hyperliquid by default, Bybit optional (`--venues`). Binance and Bybit are geo-blocked in some regions (including the US); a failing venue is skipped, not fatal. Funding rates are the venue's current per-interval rate, not annualized, and intervals can differ by venue. This is a point-in-time snapshot, so history builds only if you run it repeatedly.

## 19) Crypto options and GEX (Deribit)

`market_data_pipeline/collectors/deribit_options.py` pulls the public Deribit option book for BTC and ETH (SOL is selectable but Deribit may not list options for it), filtered by moneyness and expiry like the equity options collector. It writes `deribit_options.parquet` and, through the existing `compute_gex`, `gex_crypto.parquet`.

Differences from equity GEX: Deribit's book summary has no Greeks, so gamma is derived from mark IV with Black-Scholes (zero rate); the contract multiplier is 1 coin rather than 100 (carried in a `contract_multiplier` column, which `compute_contract_gex` honors when present); and the same dealer-positioning assumption applies (long calls / short puts), which is an estimate for crypto too.

## 21) Confluence report

`market_data_pipeline/reports/confluence_report.py` builds `data/Confluence_Report_<date>.xlsx` from the parquet files already in `data/`. It never runs a collector or transform: refresh the parquets separately, then run the report. Required files: `momentum`, `gex_data`, `options_data`, `swing_structure_1h`, `swing_structure_4h` (a missing one exits with the command that produces it). Optional: `gex_crypto`, `deribit_options`, `perps_data`, `insider_trades`, `cot_data`, `news_data` (missing ones are listed under Known Gaps). Every threshold lives in `confluence_report_rules.yaml`.

Tabs: `Summary & Ratings`, `Momentum & Delta` (full ranked universe, cash-like excluded), `Resistance Ladder Detail`, `Methodology` (rules plus a generated Known Gaps list).

- **Names shown:** the `focus` list in the rules file plus every ACCELERATING name.
- **Rating (first match wins):** DECELERATING = SELL / AVOID; no GEX = HOLD (data gap); MIXED = HOLD; ACCELERATING with NEGATIVE_GAMMA/NEAR_FLIP or a 4H DOWNTREND = HOLD; ACCELERATING + 4H SIDEWAYS + spot within 1% of the call wall = WATCH; otherwise ACCELERATING = BUY. Missing momentum history or 4H structure = `HOLD (unclassified, review manually)`.
- **Primary target:** the reported call wall while it is above spot; once cleared, the strike with the most total call open interest between 1% and 12% above spot. Stretch target: the biggest cluster from 12% to 20% above spot, shown only if its OI is at least 75% of the primary's. SELL names and names without GEX carry no target. This is an open-interest proxy, not a re-run gamma calculation.
- **Notes:** built from templates keyed on the rating reason, so the same inputs give the same text. Crypto notes add perp funding; a name gets an insider-selling flag when it has at least 20 open-market sells by at least 3 insiders in the last 90 days (Form 4 data lags, so this uses the newest data available).
- **Optional LLM notes:** `--llm-notes` (needs `uv run --with anthropic ...` and Anthropic credentials) asks Claude to rewrite only the Note column. Ratings and targets are never touched, a rewrite that drops any number from the rule-based note is discarded, and the rule-based note stays in an extra column. Not reproducible run to run.
- **Vintages:** the header shows the momentum (price close) and GEX/options dates and warns when they differ.

## 22) News data

`market_data_pipeline/collectors/news.py` writes `data/news_data.parquet` (sheet `News Data`) from Alpaca's news API, which serves Benzinga articles for equities and crypto pairs with the same credentials as the price collector. It is a headline feed for context, not a signal: no article bodies are stored.

- **Which tickers:** `--symbols` if given, otherwise the same picks the confluence report shows (focus list plus ACCELERATING names, via `market_data_pipeline/picks.py`).
- **Rows:** one per (article, requested ticker), so a multi-ticker article appears once for each pick it was fetched for. Fields: `id, ticker, headline, summary, source, url, author, created_at, updated_at, tagged_symbols, n_tagged, relevance`. Times are UTC. Text is cleaned (HTML entities unescaped, replacement characters dropped).
- **Relevance:** `HIGH` when the headline names the ticker, a built-in crypto name (Bitcoin, Ethereum/Ether, Solana) or a company name from `news.aliases` in `confluence_report_rules.yaml`; `MEDIUM` when the article is tagged to the ticker and to at most `news.max_tagged_for_high` (3) symbols; `LOW` otherwise (multi-ticker roundups). The feed has no company names for equities, so a pick with no alias is only HIGH when its ticker appears in the headline. Relevance is fixed when an article is fetched, so after changing aliases, re-run the collector.
- **Storage:** each run appends to the file and keeps one row per (id, ticker), preferring the latest `updated_at`; rows older than `--keep-days` (90) are pruned. `--days` (7) and `--limit` (50 per ticker; alpaca-py pages internally) control each fetch. Newest coverage is uneven: quiet weekends and less-followed names return few articles.
- **In the confluence report:** a `News` column shows up to 3 headlines from the last 7 days before the newest article (headlines naming the asset first, then MEDIUM ones), and the header shows how far the news goes. When a BUY/WATCH has a headline that names the asset and matches a negative keyword (or a SELL / AVOID has one matching a positive keyword) the cell starts with `REVIEW NEWS: ...`. The keyword lists are in the rules file. This is a keyword screen, not sentiment analysis, and ratings and targets never change.

## 23) Location levels (entry, stop, target)

`market_data_pipeline/transforms/location.py` reads daily bars from `price_data.parquet` and writes
`data/location_levels.parquet` (sheet `Location Levels`, one row per symbol) and `data/location_zones.parquet`
(sheet `Location Zones`, every detected zone). It implements the price-action rules in
`reference/Location Confluence Playbook.md`, which until now were documented but not built
(`location.status` in `reference/orderflow_strategy_rules.yaml`). Everything is arithmetic on daily bars: no
intraday data, no confirmation layer, and no tracking of real open positions.

### Dealing range
The last confirmed swing low to the last confirmed swing high, using the same `find_swing_points()` helper as the
intraday swing structure (3 bars each side). The range is graded into quadrants: `range_ce` is the 50% midpoint,
`range_ote` the 3/4 completion reference, and `spot_pos_in_range` says where price sits inside it. A value above 1
means price has broken out above the range, in which case CE and OTE sit below spot and cannot be targets - the
options-derived wall carries the target instead.

### Zones (fair value gaps)
A three-candle imbalance: for support, candle 1's high below candle 3's low, with the zone spanning those two
prices. `candle1_stop` is candle 1's low (the bar *before* the imbalance), which is where the stop goes - not the
zone's own boundary. A zone is `filled` once a later bar trades fully through it; a bar that only dips in leaves it
live, since the playbook treats unfilled gaps as standing reference levels. Each zone is then graded on two
independent qualifiers.

- `has_displacement` is the playbook's own test: the middle candle's body exceeded `displacement_atr_mult` x ATR(14)
  *and* it broke the prior 5 bars' extreme.
- `has_volume_confirmation` is an addition, **not** an ICT concept - none of the source transcripts framed
  displacement in volume terms. It means the same candle's `volume_ratio` reached `volume_confirm_mult`, where the
  ratio is the candle's volume over the mean of the previous `volume_baseline_window` bars. The baseline excludes
  the candle itself (the same convention as the volume floor below), otherwise a spike partly absorbs itself into
  the average it is measured against.

`zone_quality` combines them: `STRONG` clears both, `CONFIRMED` clears one, `PLAIN` clears neither. They overlap
only partly - on current data 62 of 365 zones clear displacement and 57 clear volume, but only 25 clear both - so a
CONFIRMED zone backed by volume alone is real evidence, just weaker than a STRONG one. Neither is a hard filter:
entry selection prefers the best quality available among zones within `max_pullback_pct` of spot, and falls back to
the nearest zone otherwise. Quality is weighed only inside that band, so a textbook zone 30% below spot never beats
a workable one 2% below.

### Entry, stop and target
- **Entry** is the zone's near edge (`zone_high` for a long) - the playbook's "enter at the edge, not the midpoint".
  The `Zone` column on the Summary tab reports the chosen zone's quality, so it is visible whether an entry rests on
  a confirmed zone or a common gap.
- **Stop** is `candle1_stop`. This runs wider than an ATR stop would; `risk_pct` reports how much wider, and
  `Stop ATRs` reports the same distance divided by ATR(14). Risk % is not comparable between names - 2% is a wide
  stop on a quiet ETF and noise on a volatile chip stock - whereas Stop ATRs puts every name on one scale. Values
  below `min_stop_atr` (default 1.5) are highlighted: the stop sits inside a single normal day's movement, so
  ordinary churn takes it out before the idea has been tested. Note the tension with zone quality - a STRONG zone
  comes from a large displacement candle, so its candle-1 stop is naturally further away and its R:R naturally
  worse. Compare R:R only between names with similar Stop ATRs.
- **Target** is whichever reference sits nearest above both entry and spot: CE, OTE, the range high, an untested
  ("smooth") swing high, the gamma wall, or the OI cluster. The `Target From` column names which one was used.
  CE (50%) is the conservative first target to partial into and OTE (75%) the completion reference, so a name
  trading below its midpoint targets CE and steps up to OTE once through it. A level price has already traded
  through is never offered, which is why names that have broken out above their range skip CE and OTE entirely.
- **R:R** is `(target - entry) / (entry - stop)`, written into the workbook as a live formula so editing entry or
  stop recalculates it.
- **Size** risks `sizing.risk_per_trade_pct` of `sizing.account_size` between entry and stop, capped at
  `sizing.max_position_pct` of the account. Whole units, or fractional to 4dp when one unit already exceeds the
  risk budget (BTC). Set `account_size` in `confluence_report_rules.yaml` to your real number; nothing else reads it.

### What the two-year backtest changed (2026-09-29)

The first backtest ran on ~90 days and concluded the zone entries lost money at every reward target. Deepening the
daily history to two years (22,420 bars, 3,988 zone trades instead of 338) reversed several of those readings, and
three things changed as a result.

- **Momentum now drives position size.** Score above 0.2 returned **+0.210R** (n=752), 0 to 0.2 **+0.028R**,
  negative **-0.194R** — monotonic and well sampled. It is the one feature that has ordered outcomes consistently,
  so it sets the base size tier rather than acting as one demerit among several.
- **Zone quality no longer changes size.** Crossed against momentum quintiles it does not order: in the top
  quintile PLAIN (+0.225R) beat CONFIRMED (+0.108R), and in Q3 PLAIN beat CONFIRMED again. Its apparent marginal
  edge was riding momentum. Quality is still shown, because it is how the entry and stop get placed, but sizing on
  it would be sizing on noise.
- **The stop threshold moved from 1.5 to 1.0 ATR.** Only stops under 1 ATR were clearly bad (-0.114R, n=1350);
  between 1.0 and 2.5 the effect is flat (-0.002R and -0.009R). The old 1.5 cutoff was penalising setups that
  measured fine.

Also worth knowing: **the overall picture is far less bleak than the 90-day run suggested.** All zones at 1R now
score -0.039R rather than -0.224R, and at 2R and 3R they are positive (+0.004R, +0.052R) — widening the target
helps, the opposite of the earlier finding. The random control still beats the average zone entry (+0.141R), but
zone entries taken on high-momentum names beat the control. **Displacement is dead**: -0.036R with it versus
-0.039R without, on 3,943 trades. It has no effect and never did.

These rules are **provisional**. Two years across one bull-dominated stretch is better than three months but it is
not proof, and the report says so on both the Daily Read and the Backtest tab.

### Backtest: has any of this worked?

`market_data_pipeline/transforms/backtest.py` scores the pipeline's own signals against the daily bars collected
so far and writes `backtest_zone_trades.parquet` (one row per historical trade), `backtest_summary.parquet` (the
scorecard) and `backtest_momentum.parquet`. It reads only `price_data.parquet`, so it re-runs with the daily
refresh and its sample grows every day.

**Zone trades.** Every bullish zone in history, entered at the near edge the first time price trades back into it,
stopped at candle 1 - exactly the rule the report reports. Each trade is resolved at 0.5R, 1R, 1.5R, 2R and 3R.
A bar that spans both the stop and the target counts as a **loss**: daily bars cannot say which came first, and
assuming the good outcome is how a backtest flatters itself.

**Expectancy** is R per trade (`win_rate x reward - loss_rate`). Positive makes money, negative loses it. The
**random-entry control** is the bar any signal has to clear, because a signal worse than random is worse than no
signal. Its stop widths are **sampled from the distribution the signal itself used**, and the median is printed in
the bucket label. A fixed stop width is not a like-for-like comparison: tighter stops get hit more often whatever
the entry, so an earlier fixed 1.5-ATR control overstated the zones' underperformance by roughly 28%.

The **"Momentum quintile x Zone quality"** view is the one that decides whether a second feature earns anything
once momentum is held fixed. Marginal buckets can order convincingly and still be worthless in combination, so any
change to ranking or sizing gets justified against the crossed view rather than the one-feature tables.

**Momentum** is scored separately, on non-overlapping windows only. Overlapping windows inflate the sample and
manufacture signal that is not there; with 60-90 bars per name there are only a few hundred genuinely independent
observations, and the table reports that count.

Only price-derived features can be tested: the options layers (GEX regime, walls) are a daily snapshot with no
history, so nothing in the report that depends on them has been validated at all.

**Standing caveats:** a few months in one regime, trades clustered by name and date so they are far from
independent, no transaction costs, and the mechanical entry tested in isolation from the discretion the playbook
assumes. Treat the scorecard as a sample, not a verdict.

### Backtest: the ICT target-exit test (2026-10-01) - what Rank is actually built on

The scorecard above resolves every trade against a synthetic R-multiple (0.5R, 1R, 2R, 3R...) - useful for asking
"how far should a target sit", but it is not the question that matters for a position meant to be held to a key
level: does price actually get there. `run_target_backtest()` (same module) answers that directly. Same zones, same
entry/stop, but each one is resolved against the real level a live plan would have offered at that moment - CE,
OTE, the dealing-range high, or the nearest smooth swing high, whichever sat nearest above price - computed
point-in-time from only the bars on record by then, the same no-lookahead discipline as everything else here. GEX
wall / OI-cluster targets are excluded: those are a same-day options snapshot with no history, so there is nothing
to reconstruct at a past bar. Writes `backtest_target_trades.parquet` and `backtest_target_summary.parquet`.

Because the reward varies trade to trade (whatever the real level happened to be, not one fixed multiple),
expectancy here is the mean of each trade's own realized R - the target's reward multiple when it won, -1R when the
stop came first - rather than `win_rate x one reward`. The control is matched on both stop **and** reward,
sampling each from the zones' own observed distributions, for the same reason the original control matches stops:
a control that targets differently than the signal is not a fair fight.

**This backtest, not the R-multiple one above it, is what `Past edge` and `Rank` on the Summary tab are built on**
(`reports/daily_read.py`'s `historical_edge`). Concretely it averages whichever of these the setup falls into:

- `Momentum at entry (to target)` - the same momentum bands as everywhere else in this pipeline.
- `Zone quality (to target)` - STRONG/CONFIRMED/PLAIN. Re-included in ranking here: the question "does zone quality
  matter" is more honestly asked against a real exit than a synthetic one, and the first run of this test puts
  STRONG at +0.238R (n=9, too small to lean on hard, but not zero).
- `Weak close on entry (to target)` - the live `zone_status` read maps onto this: a FAILED entry zone (the chosen
  level has already shown a body-close through its midpoint - see "Zone status" above) reads as the weak/"True"
  bucket, RESPECTED as the clean/"False" one. UNTESTED/FILLED have no equivalent bucket in the backtest (it only
  ever measures the very first touch) and are skipped rather than forced into one. On the first run this remains
  the standout: clean +0.175R vs weak -0.534R.

**Stop width plays no part in this at all**, by design - it is a risk-sizing input (how big a position to take,
still handled by the Funnel's stop-width demerit) not a reason one setup should out-rank another. Ranking now rests
entirely on momentum plays and the ICT read, per the measured target-exit result rather than the fixed-R one.

### Reading the Summary tab

The Summary is the daily read, and the only sheet you need to open. Fifteen columns, in the order you use them:

- **Rank / Ticker / Read** - rank is by `Past edge`, best first; `Read` is one sentence on the technical picture,
  e.g. "Momentum building into a strong level, stop has room; unstable options regime".
- **MOMENTUM** - `Trend` (Speeding up / Mixed / Slowing down) and `Score`. Is it moving?
- **LEVELS (ICT)** - `Level` (Strong / Medium / Weak), `Buy at`, `Stop`, `Target`, `Stop room`. Where would you act?
- **RISK** - `Shares`, `Cost $`, `Risk $`, `Reward:Risk`. What would it cost?
- **EVIDENCE** - `Past edge`: the average measured expectancy of the ICT target-exit backtest buckets this setup
  falls into - momentum and the ICT read, never stop width (see "the ICT target-exit test" above) - with the
  smallest supporting sample, e.g. `-0.11R (n>=68)`. Green when positive, red when not.

**The sheet describes and ranks; it does not instruct.** There is no "buy" column, because no signal here has yet
shown an edge that would justify one. `Past edge` is measured, not predicted, and it is an average of buckets -
not a forecast for that name.

Names with no level to trade against carry no edge and rank last. The raw technicals - the rating waterfall, GEX
regime, 1H/4H structure, setup state, Risk %, target source, insider and news - are on the **Detail** tab.

### The funnel: from universe to position size

The Summary tab is ordered so that reading left to right walks the decision, and the **Funnel** tab shows the same
walk gate by gate. Stages run in order and the first hard gate wins, the same first-match-wins shape as the rating
waterfall:

| Stage | What it asks | Hard gate? |
|---|---|---|
| `DELTA` | Is momentum still building? | Yes - DECELERATING is SKIP |
| `RATING` | Does the waterfall say BUY? | Yes - SELL is SKIP, anything else is WATCH |
| `REGIME` | How hard should this be leaned into? | No - demerit |
| `SETUP` | Is there a live level to enter on? | Yes - NO_SETUP / RESISTANCE_ONLY is SKIP, EXTENDED is WATCH |
| `ZONE` | Is there evidence the level holds? | No - a PLAIN zone is a demerit |
| `STOP` | Is the stop outside daily noise? | No - under `min_stop_atr` is a demerit |
| `RR` | Does the reward justify the risk? | No - R:R ranks, it never rejects |
| `FLAGS` | Anything independent arguing against? | No - insider clusters and news flags are demerits |

`Stage` on the Detail tab records which test a name failed first: `DELTA` (momentum), `RATING` (the waterfall),
`SETUP` (nothing to enter on), or `CLEARED` for a name that passed every test. The Summary says the same thing in
words instead, under `Why`.

A **hard gate** stops the name and `Stage` records where. Everything else is a **demerit**, which halves the
suggested size (`demerit_multiplier`, floored at `min_multiplier`) rather than overriding an earlier stage - the
rule is scale down, never override. `Action` maps from what survives: 1.0 FULL, 0.5 HALF, 0.25 QUARTER, below
that MINIMAL. The `Why` column lists the demerits applied.

Two consequences worth knowing. The regime demerit is currently unreachable for a BUY, because `rating_rules.rate()`
already blocks NEAR_FLIP and NEGATIVE_GAMMA from reaching BUY - it is kept so the funnel stays correct if those
rules change. And because a STRONG zone comes from a large displacement candle, its stop is naturally wider and its
R:R naturally worse, so it is entirely normal for no name to reach FULL; the Funnel tab says so outright when that
is the case.

### Sizing in dollars

`Size` is units, `Position $` is `entry x size` (capital deployed) and `Risk $` is `(entry - stop) x size` (what a
stop-out costs). On the Summary all three are **post-demerit**; the Funnel tab shows the full-risk figures beside
them. A name stopped at a hard gate shows no size at all rather than a position of zero. A size cut never rounds down to
nothing either: one unit is the floor, so "buy quarter" always has something to buy, and `Risk $` reports what that
single unit actually risks.

`Risk $` coming in under the per-trade budget is not an error - it means the `max_position_pct` cap bound before
the risk budget did, which happens on tight-stop names. Account inputs come from `ACCOUNT_SIZE`,
`RISK_PER_TRADE_PCT` and `MAX_POSITION_PCT` in `.env`, falling back to the `sizing:` block in
`confluence_report_rules.yaml` and then to built-in defaults, so the real number need not be committed. Every
dollar column scales directly off `account_size`.

### Setup states
`AT_ENTRY` (price is in the zone), `WAIT_PULLBACK` (the zone is below spot but within `max_pullback_pct`),
`EXTENDED` (the nearest zone is further below than that - structure exists, but chasing here is not the plan),
`NO_SETUP` (no live support zone), `RESISTANCE_ONLY` (a SELL / AVOID name, which gets overhead supply only and no
trade), `NO_LEVELS` (the symbol is absent from the levels file).

### Smooth vs jagged
A swing level stays `SMOOTH` (untested liquidity, still a draw) until a later bar trades through it, after which it
is `JAGGED` and stops being a target. `nearest_smooth_high` feeds target selection.

### Zone status (body-vs-wick respect test)
From `reference/Location Confluence Playbook.md` (source 6): a wick is allowed to pierce into a zone's far half once
price has traded back into it - that alone does not invalidate the zone. What matters is the candle **body**: if a
later bar's close goes past the zone's midpoint, the zone is read as failed even though it may not be `filled` yet
(price has not fully cleared the far edge). This is an earlier, softer warning than `filled`.

`zone_status` on each row of `location_zones.parquet` is one of `UNTESTED` (price has not traded back into the zone
since it formed), `RESPECTED` (it has, and every close since has stayed on the entry side of the midpoint), `FAILED`
(a close has gone past the midpoint), or `FILLED` (the existing, stricter condition). `entry_zone_status` on
`location_levels.parquet` carries this for the chosen entry zone specifically, and the Key Levels tab's `Status`
column shows it for every live zone, with `FAILED` highlighted. **Selecting an entry only excludes `filled` zones**,
so a name can be trading from a zone that already reads `FAILED` by this stricter test - when that happens the Daily
Read says so ("this level already had a close back through its midpoint") rather than only the Status column
carrying it.

Measured on the backtest (`transforms/backtest.py`'s point-in-time version, `weak_entry_close`: does the entry
bar's own close sit past the zone's midpoint, using only same-day information so it stays free of lookahead): a
clean entry close returned **+0.141R**, a weak one **-0.642R** (n=163 / 134) at a fixed 1R target - see the Backtest
tab's "Weak close on entry bar @ 1R" view. The same point-in-time feature is also measured in the ICT target-exit
backtest ("the ICT target-exit test" above), where it remains the standout result and is one of the three things
`Past edge`/`Rank` actually average - see that section for the current numbers.

### Inside day
`inside_day` on each name's levels row is `CLASSIC` when the most recent bar's full range sits inside the prior
bar's range, `BODY_ONLY` when only its body (open/close) does while the wicks extend beyond it, or `None`
otherwise (reference doc, sources 7/8). Read as compression ahead of an expansion move; shown on the Key Levels
tab's header line for each name. Informational only - which direction the expansion goes is read from the zone and
momentum picture elsewhere, not guessed from the inside day alone, and it does not feed the rating or sizing.

### Two things the reference doc update flagged that were deliberately left alone
- **The swing-point definition.** The doc's "formal 3-candle swing" (sources 7/8) requires only 1 bar on each side
  (a candle whose high/low beats its immediate neighbor); this pipeline's `swing_window` requires 3. The looser
  definition fires on far more, less significant pivots. Not switched to match it - that would change the dealing
  range, the smooth/jagged read and every backtested zone at once, and nothing has measured whether it helps.
- **Fixed target vs. trailing stop.** The doc itself flags an unresolved conflict: one source argues against a
  fixed OTE/CE target entirely, favoring a trailing stop with no predetermined take-profit, on its own data that
  most winners kept running past target. `select_target` still resolves to a fixed price. The doc leaves the
  choice to the user; it has not been made, and `position_levels.py` says so at the point where it matters.

### Caveats
Swing confirmation lags by the swing window, so the last few days cannot anchor a range and a fast trend leaves the
range stale. Equities carry about 62 daily bars and crypto 90, enough for the 25-day scan but thin for more. These
are candidate levels for names the ranking already selected: they never change a rating, and the pipeline still has
no position tracking, so nothing here knows what you actually hold.

### Positioning screen and ICT levels (Summary tab)

The Summary tab is a positioning screen. Step 1 gates on trend and momentum: the 4H swing structure sets the direction
and the momentum score must agree (uptrend + positive = LONG, downtrend + negative = SHORT, else NO TREND). Step 2 runs
nine layers only for names that pass, each voting + / - / 0 / n/a against that direction: 1H structure, momentum delta,
gamma regime, call-wall room (long only), spot vs the opens, last completed day's volume, speculator COT (metals only,
mapped by `positioning.cot_markets`), perp funding (crypto, one snapshot) and insider/news flags (against only). Step 3
is conviction: agree minus against over the layers that have data, HIGH at >= 4, MEDIUM at >= 2. Logic is in
`reports/positioning.py`; thresholds are the `positioning:` block of `confluence_report_rules.yaml`. None of it is
backtested and none of it changes rating, size or the measured-edge rank. Sort order is screen pass, conviction, rank.

`transforms/ict_levels.py` writes `data/ict_levels.parquet` (one row per symbol) from the 5m and 1H bars: midnight, 8:30
and 9:30 New York opens, weekly and monthly opens, the last completed Asia (20:00-24:00 ET) and London (02:00-05:00 ET)
highs/lows with a SWEPT/UNTESTED flag, the nearest untouched equal highs/lows (1H swing points within `eq_atr_mult` of
1H ATR), and last-day relative volume. Equities have no overnight bars, so their midnight open and Asia/London ranges
are blank (`min_session_coverage`). Run it before the report: `uv run python -m market_data_pipeline.transforms.ict_levels`.
The ICT Levels tab lists every level against spot in %, ATRs and side.

### Flow indicators (Summary tab, shown beside conviction)

`transforms/flow_indicators.py` writes `data/flow_indicators.parquet` (one row per symbol): CMF (20), MFI (14) and an OBV-vs-price
divergence signal from the daily bars; relative strength against a benchmark (SPY, or BTC/USD for other crypto; `flow.benchmarks`
overrides); the 5-day vs 60-day average trade size (volume / `trade_count`); anchored VWAPs from the weekly open, monthly open and
the last 1H swing low / high; and the POC and value area (70%) of the last 20 sessions of 5m bars. The latest daily bar is the day in
progress, and equity VWAP/profile include extended-hours bars. Each indicator becomes a +/-/0/n-a vote in `reports/positioning.py`
(thresholds under `positioning:` in the rules file) and the Summary's FLOW group counts them as `agree / against`. They are not part
of the HIGH/MEDIUM/LOW conviction and do not change rating, size or rank. CMF, MFI and OBV all derive from the same OHLCV, so they are one
family of evidence, not three.

`transforms/flow_backtest.py` scores the five daily votes point-in-time against 5/10/20-day forward returns (non-overlapping windows,
direction set by the momentum-score sign) and writes `flow_backtest.parquet`, which adds a Flow Backtest tab. VWAP and the volume profile
are not backtested. The sample is about two years of mostly rising markets with names that move together and no costs: treat small gaps
as noise.

## 24) How this should evolve

This project is intentionally designed so these definitions can be changed without rewriting the whole pipeline.

If the logic changes later, the places most likely to be updated are:
- `tickers.yaml` for the symbol universe (and the `cash_like` list excluded from the momentum rank)
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
- `market_data_pipeline/collectors/fundamentals.py` for the XBRL tag-fallback table and fiscal-period parsing
- `market_data_pipeline/transforms/fundamentals_ratios.py` for the ratio formulas and CapEx trend window
- `market_data_pipeline/transforms/momentum.py` for the score/delta definition, vol floor, momentum windows, and smoothing span
- `market_data_pipeline/collectors/perps.py` for perp venues and symbol mapping
- `market_data_pipeline/collectors/deribit_options.py` for crypto option filtering and the Deribit-to-GEX mapping
- `market_data_pipeline/reports/funnel.py` for the stage order, which gates are hard, and the demerit multiplier
- `confluence_report_rules.yaml` and `market_data_pipeline/reports/` for the confluence report's rating rules, target selection, note templates, insider flag and news keywords
- `market_data_pipeline/transforms/location.py` for the dealing range, zone detection and entry/stop mechanics; `market_data_pipeline/reports/position_levels.py` for target selection, R:R and sizing
- `market_data_pipeline/collectors/news.py` for news relevance rules; `market_data_pipeline/picks.py` for how the picks list is built
- `market_data_pipeline/combine.py` for workbook structure and sheet output

The goal is to keep the business definitions clear and explicit, so future data collection changes are easy to reason about.
