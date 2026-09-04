# Order Flow Strategy Playbook

**Purpose:** a repeatable pre-market/trade-review reference, built from
Chris Kmer's Environment → Location → Confirmation → Execution framework
(see `orderflow_pipeline_buildout_plan.md`), mapped honestly against what
this pipeline actually produces today. Meant to be handed to another process
(e.g. pasted into an AI session) as context for reviewing a trading day.

**Read this first:** the source doc marked several things "Built" (an FVG
framework, a Confluence Report, a Scenario Watch tab, a Gamma Wall Explainer
tab) that do not exist anywhere in this codebase — confirmed by searching the
whole repo. That status was aspirational, not real. Everything below is
checked against the actual code in `market_data_pipeline/`, not against
claims. **Environment** is now fully automated (as of the 1H/4H swing
structure work below); **Location**, **Confirmation**, and **Execution** are
still manual/discretionary.

---

## 1. Environment — Automation status: **Built**

*What the method calls for:* higher-timeframe structure (1H/4H — value
up/down/sideways, higher-highs/higher-lows) plus a GEX regime read (positive
= dampening/choppy, negative = amplifying/trending), assessed pre-market.

*What's real here:*
- `transforms/gex.py` computes a genuine GEX regime from the latest options
  snapshot: `total_gamma`, `call_gamma`, `put_gamma`, `gamma_flip`,
  `max_positive_gamma_strike`, `max_negative_gamma_strike`, and a
  `gamma_regime` classification of `POSITIVE_GAMMA` / `NEGATIVE_GAMMA` /
  `NEAR_FLIP`. Output: `data/gex_data.parquet`, "GEX Data" sheet in the
  combined workbook.
- **How to read it:** `POSITIVE_GAMMA` → expect dealer hedging to dampen
  moves, choppier/pinned character. `NEGATIVE_GAMMA` → expect dealer hedging
  to amplify moves, faster/trending character. `NEAR_FLIP` (spot within 1% of
  `gamma_flip`) → regime is unstable, character can flip intraday — treat as
  the lowest-confidence environment read.
- `collectors/price.py --timeframe 1Hour/4Hour` + `transforms/swing_structure.py`
  now give real higher-timeframe structure: a per-symbol `UPTREND` /
  `DOWNTREND` / `SIDEWAYS` / `UNKNOWN` classification from confirmed swing
  highs/lows, output to `data/swing_structure_1h.parquet` /
  `_4h.parquet` and the "Swing Structure 1H"/"4H" workbook sheets.
  `UNKNOWN` means fewer than 2 swings confirmed yet — treat as "no read,"
  not as an actual sideways call (confirmed live: `SHV`, a low-volatility
  Treasury ETF, hit this on the 4H timeframe simply from moving too little).
  Swing detection lags by design — the most recent `window` (default 3)
  bars can never be confirmed yet, since you can't tell a swing formed until
  price has moved away from it.
- `collectors/fred.py` gives a same-day-ish macro backdrop (rates, VIX,
  liquidity) as secondary environment context — not day-specific, but useful
  for "is this a week where macro dominates."
- `collectors/cot.py` gives weekly managed-money positioning — metals only
  (gold/silver), not equities, and lagged by report/release date. Useful
  context for GLD/SLV specifically, not for QQQ/SPY environment reads.

**GEX mode decision (resolves the open question in the source doc):** this
playbook uses GEX as **Mode A — regime/character only**, not as tradeable
support/resistance levels (Mode B). Mode B would require an independent
level-calling layer (Fibonacci/FVG zones, not just a trend classification)
to justify treating a wall as a real level, and that layer doesn't exist yet
— see Location below. Don't fade or target a GEX wall directly off this
pipeline's output — use the regime to set expectations, and use manual chart
reading for actual levels.

---

## 2. Location — Automation status: **Manual-only** (partially unblocked)

*What the method calls for:* Fibonacci retracement (70.5/78.8/88.6 "golden
pocket") from swing low to swing high, defining discount/premium — must sit
outside the value area, entered only after internal structure (a swing
point, ideally a liquidity sweep) forms.

*What's real here:* the swing-high/low *values* now exist —
`transforms/swing_structure.py`'s `find_swing_points()` produces exactly the
raw inputs a Fibonacci retracement would be calculated from (see Environment
above). But nothing turns them into retracement zones, value-area bounds, or
FVG detection yet. The primitive exists; the Location-specific calculation
on top of it doesn't.

*What to do instead:* read this manually on a charting platform for now.
Automating it would mean adding a Fibonacci-zone calculation on top of the
existing swing-point output — a smaller lift than before, since the
swing-detection work is already done, but still a separate build.

---

## 3. Confirmation — Automation status: **Manual-only** (the core gap)

*What the method calls for:* footprint candles — volume-at-price plus
bid/ask delta — looking for absorption (aggressive selling at an extreme,
no downside result) followed by a second failed push that fails to make a
new low.

*What's real here:* nothing. `collectors/options.py` is an end-of-day
snapshot with no intraday timestamps; no tick or quote data is collected
anywhere. This matches the source doc's own assessment — it calls this "the
core gap," and that's still accurate.

*What it would take to automate (not built, for reference):* tick-level
trade data with Lee-Ready buy/sell classification (requires an Alpaca SIP
tier, not just IEX), block/large-print detection, and ideally an
aggressor-tagged options sweep feed (a paid third-party vendor — Alpaca's
standard endpoints don't provide aggressor-side attribution). See
`orderflow_pipeline_buildout_plan.md` §5 for the phased roadmap.

*What to do instead:* read footprint charts manually on whatever platform
provides them. This pipeline currently contributes nothing to this step.

---

## 4. Execution — Automation status: **Manual-only**

*What the method calls for:* stop below the failed-seller low; targets at
POC/swing highs/psychological clusters; trail behind continued buyer
aggression; move to break-even if buyers can't reclaim the value area.

*What's real here:* nothing. This pipeline collects and derives data; it has
no trade journal, position tracking, order management, or execution logic
anywhere.

*What to do instead:* manage the trade manually per the rules below.

---

## 5. Process Discipline

From the source material, verbatim:
- Trade only the first ~1.5 hours of the NY open (sometimes the Asia
  session) — no mid-session entries.
- Hard volume floor: don't trade below 20,000 contracts per 5-minute candle
  — below that, participation is dying out and signals are less reliable.
- Hard stop after 2 consecutive losses for the session.
- One strategy only — no strategy-hopping mid-session.
- Track execution quality separately from P&L.
- Target performance band (not a promise, a reference range): ~60-65% win
  rate, ~1.8 profit factor, ~1.5R average winner.

**Volume floor — now data-backed:** `collectors/futures_volume.py` pulls
5-minute bars for `MNQ=F`/`MES=F`/`SIL=F` and flags each with
`volume_floor_met` (`data/futures_volume_5m.parquet`, "Futures Volume 5m"
sheet). Read the caveat before trusting it for a live decision: the source
(Yahoo Finance's public chart endpoint) is unofficial, runs roughly 10
minutes delayed, and Alpaca offers no alternative — this was the only free
option. Good enough for a pre-trade sanity check; not a substitute for
watching the live tape if the decision is time-critical. Everything else in
this section remains manual/discretionary — not data-dependent.

---

## 6. Known Gaps Summary

| Step | Status | Blocking gap |
|---|---|---|
| Environment | Built | GEX regime + 1H/4H swing structure, both automated |
| Location | Manual-only | Swing-point values exist; Fibonacci/FVG zone calculation on top of them doesn't |
| Confirmation | Manual-only | No tick/quote data, no Lee-Ready classification, no options-sweep feed |
| Execution | Manual-only | No trade journal or position/order management exists |

None of these gaps are secretly solved elsewhere in this repo — they're real,
and closing the remaining ones (in order of buildability) means: a
Fibonacci-zone calculation on the existing swing points next (finishes
Location — the smallest remaining lift, since swing detection is already
built), then tick classification (Confirmation Phase 1 per the source doc),
then a paid options-sweep vendor if the self-built proxy isn't enough
(Confirmation Phase 3). See
`orderflow_pipeline_buildout_plan.md` §5-6 for the fuller roadmap and next
steps on that build-out.
