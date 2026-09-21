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

## 23) How this should evolve

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
- `confluence_report_rules.yaml` and `market_data_pipeline/reports/` for the confluence report's rating rules, target selection, note templates, insider flag and news keywords
- `market_data_pipeline/collectors/news.py` for news relevance rules; `market_data_pipeline/picks.py` for how the picks list is built
- `market_data_pipeline/combine.py` for workbook structure and sheet output

The goal is to keep the business definitions clear and explicit, so future data collection changes are easy to reason about.
