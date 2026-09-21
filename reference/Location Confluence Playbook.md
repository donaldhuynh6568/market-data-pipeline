# Location Confluence Playbook

**Purpose:** a rules reference for the price-action layer this pipeline's
Location step is missing (see `orderflow_strategy_rules.yaml`, where
`location.status: not_built`). Source material: four livestream
transcripts. The first is a discretionary trader running a
suspension-block / quadrant / FVG framework, heavily overlapping with but
distinct from ICT proper (that trader explicitly contrasts his own
approach against ICT's). The second through fourth are ICT-proper
vocabulary (sessions, kill zones, displacement, PD arrays, consequent
encroachment, dealing range). Terms below are kept as each source used
them; where sources disagree or use different words for the same idea,
both are noted.

**What this framework is actually for:** it is not an indicator system.
The core idea, stated plainly: map where untested liquidity sits before
a session starts (time first), then require proof that price is
genuinely moving to claim it (displacement) before treating a level as
relevant (price second). Everything below is in service of that
sequence: anticipate a level and a bias in advance, then wait for
confirmation, rather than reacting to price candle by candle.

**Read this first:** none of this is coded yet. This doc is the rules
layer only, written so it can be formalized into a
`transforms/location.py` later without re-deriving the definitions from
the transcripts. Nothing here is aspirational status like the prior
playbook flagged; it's a plain "not built yet."

**Relationship to the rotation strategy:** this is not a standalone
signal generator for the 25-asset rotation basket. Rotation ranking stays
volatility-adjusted momentum with the 200-day trend filter, per
`rotationstrategy.md`. These concepts are a candidate entry/exit timing
overlay on top of a name the rotation ranking has already selected.
Concretely, the transferable piece for a monthly-rebalanced book is the
**daily-timeframe version** of the liquidity-mapping logic (section 0).
Session/kill-zone timing itself (the intraday sub-windows) does not
transfer to a monthly rebalance; that stays scoped to the separate
intraday order-flow strategy. Don't let Location signals override
momentum ranking, only time entries/exits around it.

---

## 0. Quick reference: reading entry/exit levels off a chart

This is the whole model, compressed to what's needed to actually place a
stop and a target. Everything past this section is supporting definition
and detail; start here.

**Three things you need, and only three (source material's own
simplification):**
1. **Liquidity** — where is price drawing to.
2. **Risk framing** — where does the stop go.
3. **One entry mechanism** — how you time getting in.

**The walkthrough:**
1. **Define the dealing range.** Pick the high and low that bound the
   move you care about (a recent swing high/low, a session's range, a
   suspension block). This is your canvas; everything else is read
   relative to it.
2. **Classify recent highs/lows inside it as smooth (untested) or
   jagged (already swept).** Smooth levels are the draw. Once run
   through, a level is jagged and stops being a target.
3. **Apply the 3-way decision hierarchy:** was there a recent sell-side
   stop hunt, a buy-side stop hunt, or neither? Find the first imbalance
   in the return leg from that hunt, then classify the far level it's
   heading toward as a single high/low or a relative-equal high/low
   (grade the anchor candle's wick if it has one — see section 1).
4. **Require displacement.** Only treat a gap as a real FVG (not a
   common gap) if it formed on a fast, directional break of a prior
   high/low.
5. **Entry:** at the FVG's near edge, not a deeper fill at CE. Don't
   wait greedily for a better price.
6. **Stop:** candle 1's low (bullish) / high (bearish) — the
   pre-imbalance candle, not the FVG's own boundary.
7. **Target — use 3/4 of the dealing range as the optimal trade entry
   / primary target reference (OTE)**, not just CE. CE (50%) is a
   conservative first target to partial into; the far extreme of the
   range is a stretch target. A move can complete at 3/4 of the range
   and never return to the starting consolidation — don't assume it has
   to revisit the midpoint on the way.
8. **Pick one entry mechanism and stick with it for the setup.** Don't
   require order blocks, breakers, model 2022, COT, and seasonality to
   all agree simultaneously, and don't hop between them mid-session when
   your chosen one hasn't fired yet. If your model didn't trigger, the
   answer is to do nothing, not to switch models.

---

## 1. Definitions

**Dealing range:** the high and low that bound the price action you're
currently analyzing — a recent swing high to swing low, a session's
range, or a suspension block. Same underlying idea as "suspension block"
and "session range" elsewhere in this doc, generalized: whatever range
you've defined, everything else (quadrant grading, OTE, liquidity
targets) is read relative to it.

**Optimal trade entry (OTE), 3/4 rule:** within a defined dealing range,
3/4 of the range (measured from the range's origin toward its target
side) is the reference point for where a move is likely to reach, and
often complete, without needing to revisit the original consolidation.
Distinct from CE (50%, the conservative/first target) — OTE is the
higher-confidence completion zone for a directional move started from
one end of the dealing range.

**Volume imbalance:** the gap between one candle's body close and the
next candle's body open (a body-to-body gap, not wick-to-wick).

**Fair value gap (FVG):** the gap between a candle's wick and a
non-adjacent candle's wick two bars later (the standard three-candle
imbalance: candle 1 high/low vs. candle 3 low/high, with candle 2's range
not overlapping).

**Common gap vs. real FVG (displacement filter):** not every gap
qualifies as a PD array. A "common gap" is a slow, low-conviction gap
with no directional force behind it; it's background noise and should be
excluded. A real FVG requires **displacement**: a fast, directional run
through a prior high/low, ideally at a structurally meaningful time (a
kill-zone open, in the source material). This is a validity filter to
apply on top of the plain three-candle FVG definition above, not a
replacement for it.

**FVG entry and stop mechanics (precise):** in the 3-candle FVG (candle 1
pre-imbalance, candle 2 imbalance, candle 3 closes and frames it):
**entry** is at the FVG's near edge, not a deeper fill at CE — waiting
greedily for a better price at CE or lower is the stated failure mode
(price often tags the edge by a tick and runs away unfilled). **Stop** is
candle 1's low for a bullish FVG, candle 1's high for a bearish FVG —
the candle *before* the imbalance, not the FVG's own high/low.

**Decision hierarchy for which level to grade:** given a higher-timeframe
bias, this is a three-way, mutually exclusive choice, not a checklist to
satisfy all at once: (1) check what recently happened to the range — was
there a sell-side stop hunt, (2) a buy-side stop hunt, or (3) neither.
Then locate the first imbalance in the return leg from that hunt, and
classify the far level it's heading toward as either a single high/low
or a relative-equal high/low. That's the level to grade. Confluence
stacking beyond this is optional, not required — one clean setup (bias +
displacement + a graded level) is sufficient; don't require order
blocks, model 2022, COT, and seasonality to all agree simultaneously
before acting.

**Gaps as magnets:** FVGs and opening-range gaps (regular-trading-hours,
new-day, new-week) tend to get revisited even after price has moved away
for a while — treat an unfilled gap as a standing reference level, not a
one-time target that expires.

**One model at a time:** pick a single entry mechanism (an FVG, an order
block, a breaker) per setup and wait for it. If it doesn't fire, do
nothing — don't switch to a different model mid-session hoping one of
them works. Model-hopping is the stated cause of missing the trade that
would have worked while chasing the one that wouldn't have.

**Suspension block:** a daily range bounded by two volume imbalances,
one sitting at the high of the range and one at the low. Acts as a
container; price is expected to work inside it until one side gives way.

**Quadrant grading:** any range (a swing range, a single candle's wick,
a full session's range, or a dealing range) divided into quarters. Upper
quadrant, midpoint, lower quadrant, and the extremes. The midpoint is
called **consequent encouragement / consequent encroachment (CE)**.
Grading applies to individual candle wicks the same way it applies to
swing ranges, session ranges, or dealing ranges.

**Relative equal highs/lows, with anchor-point rule:** two highs (or two
lows) close to equal, with one slightly beyond the other. The **anchor
point** is the higher of the two highs (or the lower of the two lows);
that's the real target, not just "two highs that look close." Read as a
resting liquidity pool and a high-probability sweep target. **Refinement:
if the anchor candle has a wick**, don't assume the whole level sweeps.
Grade the wick into quadrants (CE, upper/lower quadrant) and expect price
may only reach the wick's CE or lower quadrant before reversing.
Failing to even reach CE on the approach is itself read as a weakness
signal (the move can't breach the midpoint of the level it's attacking).

**Session vs. kill zone:** a session is the full trading window for a
region (Asia ~7pm-5am ET, London ~2am-11am ET including its own
extension, NY 9:30am-4pm ET). A kill zone is a narrower, higher-probability
sub-window inside a session (London kill zone 2am-5am ET, NY pre-market
7am-9am ET, NY open 9:30am-10am ET). 8:30am ET (common economic-release
time) behaves like a third reset point alongside the 9:30 open, whether
or not there's actual news that day. The distinction matters because
smooth (untested) highs/lows have to be scanned across the **entire
session range**, not just within the kill zone; the kill zone is where
entries are timed, not where the liquidity search is limited to.

**Smooth vs. jagged:** an untested high/low, with no repeated wicking
through it, is unclaimed liquidity and the highest-probability draw for
price. Once price has run through and reacted off it, it becomes
"jagged" and stops being a target.

**Turtle soup:** a short-term high forms, price makes a marginally higher
high that fails to hold, then pulls back into range. The reversal itself
doesn't need to be a full trend change, just that failed-breakout
character.

**"Model 2022" (source-material usage):** the fractal pattern of an FVG
forming on the right side of a swing high, repeatable across timeframes
(higher timeframe or lower timeframe, same shape). Distinct from the
buy-side FVG rule below, which is the extension added on top of it.

**Order block (source material's "market maker buy/sell model"):** after
a relative equal high/low is swept, the last down-close candle before the
reversal through that level becomes the order block. Price is expected
to return to its CE before continuing in the sweep's direction.

---

## 2. Confluence rules

- **Time first, then price:** define the dealing range/session and scan
  its full extent for smooth (untested) highs/lows before looking at any
  lower timeframe. A level found this way, then confirmed with
  displacement, is the basis for a bias; don't react to a PD array found
  out of that order.
- **Displacement required before a gap is tradable:** require a fast,
  directional break of a prior high/low before treating a gap as a real
  FVG (see Common gap vs. real FVG above). This is the single biggest
  false-positive filter on the plain FVG definition.
- **Buy-side over sell-side FVG:** when a swing high forms and there is
  an FVG (or volume imbalance) on the left/buy side of it as well as one
  on the right/sell side, prefer the left-side one as the more sensitive
  target. It acts as an inversion FVG once price rotates lower through
  it. Tradeoff stated explicitly: a smaller FVG below it may get filled
  first and the buy-side target may never get hit.
- **First FVG after turtle soup, extended forward:** once a turtle-soup
  rejection completes, the first FVG that forms afterward is the one to
  carry forward as a reference level, not just use once and discard.
  Same idea as gaps-as-magnets: don't discard an unfilled level.
- **Suspension block / dealing range as containment:** while price is
  inside a suspension block or dealing range, expect it to keep testing
  both boundaries rather than trending cleanly; use the range's own
  quadrant grading (CE, upper/lower quadrant, OTE at 3/4) for expected
  turning points within it.
- **Order block confirmation:** treat a sweep of relative equal
  highs/lows as higher probability when the far-side level is only
  slightly beyond the near side (not a clean, obvious equal-high/low),
  and require price to hold above/below the swept level's close (not
  just wick through it) before treating the order block as validated.
- **Anchor-point wick grading:** apply the anchor-point + wick-quadrant
  refinement (see definitions) before assuming a relative-equal
  high/low fully sweeps; grade the anchor candle's wick and treat CE as
  the minimum bar for the move to show strength.
- **Enter at the edge, not the midpoint:** take the FVG's near edge as
  entry; don't sit on a limit order at CE hoping for a better fill.
- **Stop at candle 1, not the FVG boundary:** use the pre-imbalance
  candle's extreme (low for bullish, high for bearish) as the initial
  stop.
- **Target OTE (3/4), not just CE:** treat 3/4 of the dealing range as
  the primary completion reference; CE is a conservative interim target
  to partial into, not the finish line.
- **One model at a time:** commit to a single entry mechanism per setup;
  don't require every PD array to agree, and don't switch models
  mid-session when the chosen one hasn't triggered yet.

---

## 3. Opening range half-gap rule (intraday, not buildable yet)

- Measure the regular-trading-hours opening gap: previous session's
  settlement price vs. today's 9:30am ET open.
- Grade it the same way as any dealing range: quarter, half (CE),
  three-quarter (OTE), full closure.
- Stated frequency: roughly 70% of the time price fills half the gap or
  trades back to it by 10:00am NY.
- Direction bias comes from the higher-timeframe (daily) read: if the
  daily read favors a given direction, that bias can override the 10:00
  time cutoff and the market may not revisit the half-gap at all before
  continuing.
- If price closes outside the gap and keeps running, that's the signal
  to abandon the gap-closure idea for the session rather than waiting
  for it to come back.
- **Blocked on the same gap as the order-flow playbook's Location and
  Confirmation steps:** `collectors/price.py` only pulls
  `TimeFrame.Day`, so there is no RTH open print to measure this against.
  Needs the same intraday collector buildout flagged in
  `orderflow_strategy_rules.yaml` (`intraday_structure.status:
  not_built`).

---

## 4. Trade management (process discipline, not data-dependent)

- Take a partial when a trade has run in a straight line toward target
  and is close to it, rather than waiting for the full target.
- After taking a partial, move the stop and give it limited room (source
  material uses roughly three handles at a time on that instrument, i.e.
  small increments relative to that instrument's tick size, not a fixed
  dollar rule to copy literally).
- Ratchet the stop on every new swing low/high in the trade's favor;
  never leave a stop at a level that isn't tracking the most recent
  favorable structure. As the trade develops, "move the stop a little
  bit" is the rule; never jam it straight to breakeven just to relieve
  the discomfort of holding open risk.
- Be more conservative with stop-trailing when PD arrays are stacked
  close together (higher chance of a fast move erasing unrealized
  profit); can be more liberal once conditions are calmer.
- Position size discipline (not a price rule, but load-bearing for
  whether the price rules can be followed): before sizing a trade, ask
  whether a full loss on it would make the next trade harder to take.
  If yes, it's oversized. This determines whether stop-outs stay routine
  cost-of-business or start causing rule deviation.

---

## 5. Status summary

| Concept | Data needed | Status |
|---|---|---|
| Volume imbalance / FVG detection + displacement filter | Daily bars (have) | Not built |
| FVG entry/stop mechanics (candle-1 stop, edge entry) | Daily bars (have) | Not built |
| Dealing range + OTE (3/4 rule) | Daily bars (have) | Not built |
| Suspension block | Daily bars (have) | Not built |
| Quadrant grading / CE (swing range, single wick, session, dealing range) | Daily bars (have) | Not built |
| Buy-side vs. sell-side FVG hierarchy | Daily bars (have) | Not built |
| Relative equal highs/lows + anchor-point wick grading | Daily bars (have) | Not built |
| Turtle soup + first-FVG extension / "model 2022" | Daily bars (have) | Not built |
| Order block after sweep | Daily bars (have) | Not built |
| Smooth vs. jagged classification | Daily bars (have) | Not built |
| Opening range half-gap rule | Intraday RTH open (missing) | Not built, blocked |
| Session vs. kill-zone timing, 1H/4H swing structure | Intraday (missing) | Not built, blocked (same gap as order-flow playbook) |
| Trade management rules | N/A, discretionary | Documented, not automatable |
| Daily stop/target workflow for open positions (section 6) | Daily bars (have) + position list (missing) | Manual today, blocked on position tracking |

Everything above the half-gap section is arithmetic on data the pipeline
already collects (`collectors/price.py`, daily bars). The half-gap rule
and session/kill-zone timing are blocked on the same intraday-data gap
already flagged in `orderflow_strategy_rules.yaml`.

---

## 6. Daily workflow: identifying stop/target levels for open positions

**Goal:** each time `market_data.xlsx` is refreshed, identify a stop and
a target for each open rotation position, using only the daily bars
already in the Price Data sheet. This is doable today by manual
walkthrough of section 0; not yet a coded step.

**Steps, per open position, on each data refresh:**
1. Pull that ticker's daily OHLC for the last ~20 trading days (the
   source material's own stated lookback: "highest probability precision
   setups form in the last 20 days").
2. Define the dealing range (recent swing high to swing low relevant to
   the position's direction).
3. Classify recent highs/lows in that range as smooth (untested) or
   jagged (already swept).
4. Apply the decision hierarchy (section 1): was there a recent sell-side
   or buy-side stop hunt on this name, then find the first imbalance in
   the return leg, then classify the level it's heading toward as a
   single high/low or a relative-equal high/low (with anchor-point +
   wick grading if it has a wick).
5. Locate the first FVG with displacement in that return leg (common-gap
   filter applies).
6. **Stop** = candle 1's low/high of that FVG (per the direction of the
   position).
7. **Target** = CE (50%) of the dealing range as the conservative first
   target to partial into; **OTE (75%)** as the primary completion
   reference; the far extreme (full relative-equal high/low, opposite
   suspension-block boundary) as a stretch target only.
8. If price is inside a suspension block, use the block's own quadrant
   grading for interim turning points instead of a single stop/target
   pair.

**Known gap before this can run smoothly:** the pipeline doesn't track
open positions anywhere (`execution.status: not_built` in
`orderflow_strategy_rules.yaml` — no trade journal or position tracking
exists). This workflow needs to know current tickers, direction, and
entry price for each open rotation position on every refresh. Until
there's a Positions sheet in the workbook (or an equivalent), that list
has to be supplied alongside each `market_data.xlsx` upload.

**What this does NOT do:** it doesn't replace or feed back into the
momentum ranking. It only produces a stop/target pair for a position the
ranking has already opened. If a position hasn't been through the
rotation ranking, this workflow has nothing to act on.