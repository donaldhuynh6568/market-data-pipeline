# Analysis Strategy Spec (v0, draft)

Companion to `market_analysis_restart_spec.md`. This defines how we analyze and evaluate, so the marts can be shaped to serve it. Items marked **OPEN** are gaps to fill in.

Source material: zoomcamp modules 2 (unified dataset), 3 (time series modeling), 4 (trading simulation). Only lessons that change a design decision are carried in.

## 1. Strategy shape

Two tracks, evaluated on the same footing:

- **Track A: rule-based momentum rotation (committed).** Rank the 25-asset basket on volatility-adjusted momentum, hold the top N, rebalance monthly, with a 200-day trend filter as the first candidate overlay. No model training.
- **Track B: model-based ranking (optional experiment).** A classifier or regressor over the same features predicting forward outperformance. It earns a place only if it beats Track A net of costs on held-out data.

The course approach is per-stock binary prediction over a 30-day horizon with simulated $100 bets. Our approach is cross-sectional: choose the best names among many at each rebalance. The course's evaluation discipline carries over. Its prediction framing is only borrowed for Track B.

**OPEN:** Is Track B in scope for the restart, or deferred until Track A is validated?

## 2. Lessons carried in, and what each changes in the marts

| Lesson from transcripts | Design decision | Mart impact |
|---|---|---|
| Use returns, never absolute prices, so tickers are comparable | All features are returns, ratios, or ranks | `mart_features` holds no raw price levels as features |
| Future growth columns must never be features; a shift bug in the course target definition invalidated a homework | Targets live in their own table, built by one tested function, with a unit test on a known series | Separate `mart_targets`, never joined into the feature list |
| Declare feature sets explicitly: to-predict, to-drop, features | A feature registry file lists every column and its role | `config/feature_registry.yaml`, checked against the mart schema |
| Join grain matters: date+ticker is one-to-one, date-only market data is many-to-one | Every join declares expected cardinality and asserts it | Row count of `mart_features` must equal the spine row count after every join |
| Quarterly or monthly data joined at period start leaks the future | Slow data joins on `available_at`, not period date | Staging carries `available_at`; marts join as-of |
| Forward fill, never interpolate | Capped forward fill with a staleness counter | `days_since_update` column on filled series |
| Temporal split only; keep the last period untouched | Split is a column, assigned by date | `mart_split` (date, split) |
| Check every ticker has history in the train period | Minimum-history rule per ticker | History audit table; rule is **OPEN** |
| Trim return outliers before fitting; check class balance | Winsorize or trim for Track B fitting only, never for performance reporting | Flag column `is_outlier`, not deletion |
| Volatility must be computed on returns, not price levels | Realized vol = std of daily returns over a window | Part of `mart_features` |
| Avoid trading around earnings (irrational behavior) | Earnings proximity flag as a possible exclusion filter | **OPEN:** need an earnings-date source, none in current collectors |
| Seasonality and fixed effects (turn of month, month dummies) | Test as candidate features, not assumed | Month and turn-of-month flags in features, kept only if they pass keep-or-cut |

## 3. Features

Track A needs a small set. Candidates, to be confirmed:

- Returns at 1, 3, 6, 12 months; skip-recent-month variant.
- Realized volatility over 1 and 3 months.
- Vol-adjusted momentum score and its cross-sectional rank.
- Distance from 200-day MA; above/below flag per ticker, plus QQQ and SPY flags.
- Regime context: VIX level, optional GEX regime later.

The course generates 100+ technical indicators and lets importance sort them out. We deliberately do not, because our history is short (see section 8) and breadth invites overfitting. Indicators are added one at a time under the keep-or-cut test.

**OPEN:** Final feature list and window lengths. Whether any technical indicators (RSI, ATR, OBV) are worth testing.

## 4. Targets

- Primary: forward 21-trading-day return per ticker, and its cross-sectional rank at each rebalance date.
- Optional binary: forward return above the universe median (balanced by construction, which avoids the class-imbalance problem the course flags).
- Targets use the same horizon as the holding period, so the evaluation matches how the strategy is actually run.

**OPEN:** Horizon (21 days vs other). Return vs excess-return-over-benchmark target.

## 5. Splits and validation

- Temporal split by date, three parts: train, validation, test. The test period is not touched until the strategy is frozen.
- Course proportions: roughly 70 to 80% train, 10 to 15% validation, 10 to 15% test, over 25 years.
- Addition not from the course: forward returns overlap across consecutive days, so adjacent rows share outcomes. Sample at rebalance dates and leave a gap of at least the target horizon between split boundaries.
- Track A has few tunable parameters, so its main protection against overfitting is testing parameter robustness across a grid, not picking one best cell. The course's stable-region idea fits here: a good setting is one whose neighbors also work.

**OPEN:** Split dates, which depend on how much history we can backfill (section 8).

## 6. Evaluation: what counts as success

The key lesson from module 4: the model with the best precision (58 to 63%) earned about 4.9% CAGR, versus 11 to 13% for the S&P 500. Precision alone is not a result. So:

**Primary metrics (money):**
- Net CAGR after costs.
- Max drawdown.
- Sharpe or return per unit of volatility.
- Turnover and number of trades per rebalance.

**Secondary metrics (signal quality):**
- Hit rate against the base rate (unconditional probability of a positive period), not against 50%.
- Rank correlation between score and forward return, per rebalance date.
- Return by momentum quintile (the gradient test, including whether the top quintile fades).

**Baselines, all required:**
1. Equal-weight basket, same rebalance schedule.
2. SPY buy and hold.
3. Random selection of N names, many draws.

A signal is kept only if it beats all three out of sample, net of costs, with enough observations to trust the result. The course shows the failure case: a rule that trades 39 times in 4 years has a high precision number and no statistical weight.

**OPEN:** Minimum number of rebalances or trades before a result is trusted. Whether to benchmark against SPY only or a blend that reflects the international and crypto holdings.

## 7. Simulation rules

From module 4, adapted:

- Vectorized: returns computed by column operations, not row loops, wherever possible.
- Costs are explicit. The course uses 0.1% per side (0.2% round trip). **OPEN:** our assumed cost per trade and slippage, especially for crypto and the less liquid names.
- Report capital required and workload, not just return: how many positions held, how many trades per rebalance.
- Phase 1 sizing: fixed equal weight per position. Phase 2: compounding with reinvestment (the course defers this to a later module because trades then depend on prior results).
- Defensive rotation (GLD or cash when the trend filter fails) is tested as a switch against the always-invested version.

**OPEN:** Top N, weighting scheme, rebalance day, and whether volatility scaling is tested in phase 1.

## 8. Data sufficiency (the main risk)

The course trains on about 25 years across 33 stocks, around 136,000 training rows. Our collectors currently give roughly 90 days, and the plan targets 2 to 3 years. That is enough for Track A's rule-based evaluation but too little to train and fairly test a model.

Consequences:
- Track A can proceed once 2 to 3 years are backfilled.
- A 12-month lookback plus a 200-day MA consume a large share of 2 to 3 years before the first signal.
- Track B should wait for deeper history, or for a deliberate decision to source it elsewhere.
- Any result from a short window is provisional, which the earlier backtest spec already flags.

**OPEN:** How much daily history can Alpaca provide for stocks and for crypto? Is a second source for longer history acceptable, and does it need its own raw dataset?

## 9. Mart design implied by this strategy

| Mart | Grain | Contents | Needed by |
|---|---|---|---|
| `mart_price_daily` | ticker, date | Adjusted close, daily return, staleness flags | All |
| `mart_features` | ticker, date | Momentum, volatility, MA flags, regime joins | A, B |
| `mart_targets` | ticker, date | Forward returns and ranks, separate from features | Evaluation, B |
| `mart_split` | date | train / validation / test assignment | A, B |
| `mart_signals` | ticker, rebalance_date | Score, rank, hold flag, per strategy variant | A, B |
| `mart_sim_trades` | variant, ticker, rebalance_date | Gross and net return per position, costs | Evaluation |
| `mart_performance` | variant, split | CAGR, drawdown, Sharpe, turnover, vs baselines | Evaluation |

`mart_signals` carries a `variant` key so every candidate (base momentum, plus trend filter, plus vol scaling, model-based) lands in the same table and is compared by one query.

**OPEN:** Whether features and signals are built at daily grain and sampled at rebalance dates, or built only at rebalance dates. Daily is more flexible for weekly checks; rebalance-only is smaller.

## 10. Build order

1. Spine and price mart (after raw and staging exist).
2. Targets mart with a unit-tested target builder.
3. Features mart for Track A, with the feature registry.
4. Split table and baseline simulations (equal-weight, SPY, random).
5. Base momentum signal and simulation; compare against baselines.
6. Add one candidate at a time (trend filter, vol scaling, top-N changes), each recorded as a new `variant`.
7. Revisit Track B only if section 8 is resolved.

## 11. Open questions summary

1. Is Track B in scope now?
2. Final feature list and windows; any technical indicators?
3. Target horizon and target type.
4. Split dates and minimum-history rule per ticker.
5. Minimum trades or rebalances before trusting a result; benchmark choice.
6. Cost and slippage assumptions.
7. Top N, weighting, rebalance day, vol scaling in phase 1.
8. Alpaca history depth; any second source for longer history.
9. Earnings-date source, if the earnings filter is wanted.
10. Daily-grain versus rebalance-grain features.
