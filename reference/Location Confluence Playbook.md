# Location Confluence Playbook

**Purpose:** a rules reference for the price-action layer this pipeline's
Location step is missing (see `orderflow_strategy_rules.yaml`, where
`location.status: not_built`). Source material: eight livestream
transcripts. The first is a discretionary trader running a
suspension-block / quadrant / FVG framework, heavily overlapping with but
distinct from ICT proper (that trader explicitly contrasts his own
approach against ICT's). The second through fourth are ICT-proper
vocabulary (sessions, kill zones, displacement, PD arrays, consequent
encroachment, dealing range). The fifth is a heavily promotional "free
course" video (broker-statement flexes, prop-firm payout screenshots, a
paid-mentorship pitch, a giveaway) — the technical content is mostly
consistent with ICT-proper and is kept below, but it's weighted slightly
lower than the first four sources since confident delivery isn't the same
as reliability, and its selling context should be kept in mind. The
sixth is a live-account walkthrough from a different, less promotional
creator (mentions his own book and personal life) — genuinely ICT-proper
in substance, weighted the same as sources 1-4. The seventh and eighth
are two further sessions from that same creator (same live-account,
father/son format) — weighted the same as source 6, not lowered. Terms
below are kept as each source used them; where sources disagree or use
different words for the same idea, both are noted.

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

**Open question, not resolved here:** source 5 argues against a fixed
OTE/CE target — its stated approach is to trail the stop behind each new
swing and let a winning trade run until stopped out, rather than
partialing at CE or treating OTE as the completion reference (its own
stated data: most of its winners kept running well past target). That is
a real conflict with Section 0/2 below, which treat OTE as the primary
completion reference and CE as a conservative partial. This doc does not
pick a winner between "fixed target at OTE" and "trail and let it run" —
that's a decision for you to make, not one to blend silently. Flagging it
here so it doesn't get lost.

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
   relative to it. Use the formal 3-candle swing high/low definition
   (section 1) rather than an eyeballed pivot when it matters which
   exact bar counts as the swing point.
2. **Classify recent highs/lows inside it as smooth (untested) or
   jagged (already swept).** Smooth levels are the draw. Once run
   through, a level is jagged and stops being a target. When two
   candidate relative-equal levels are both plausible and it's
   ambiguous which to use, default to whichever is cleanest/most
   obviously equal rather than the technically-arguable one (source 6's
   simplification — see section 1).
3. **Apply the 3-way decision hierarchy:** was there a recent sell-side
   stop hunt, a buy-side stop hunt, or neither? Find the first imbalance
   in the return leg from that hunt, then classify the far level it's
   heading toward as a single high/low or a relative-equal high/low
   (grade the anchor candle's wick if it has one — see section 1).
   Before committing to a direction, sanity-check the broader liquidity
   run condition (section 1): is price in a high-resistance run (choppy,
   fighting through stacked levels) or a low-resistance run (clean,
   fast, little opposing structure)? That changes how far a move is
   likely to travel once it starts.
4. **Require displacement.** Only treat a gap as a real FVG (not a
   common gap) if it formed on a fast, directional break of a prior
   high/low. If it broke through several prior highs/lows at once
   rather than just one, it's a break-in-structure gap (BSG) — see
   section 1 — and carries higher probability than an ordinary FVG.
5. **Entry:** at the FVG's near edge, not a deeper fill at CE. Don't
   wait greedily for a better price. For a BSG specifically, the
   inflection point (see section 1) is a more precise entry reference
   than the plain near edge. Once inside the zone, apply the
   body-vs-wick respect test (section 1) to confirm it's actually
   holding: a wick may pierce toward the far half, but the candle body
   should stay on the entry side of the zone's midpoint. When a move is
   sharp enough that waiting for a pullback into the FVG risks missing
   it entirely, the sharp-reversal entry (section 1) is the stated
   alternative: enter on a stop/market order in the direction of the
   reversal rather than waiting for the standard pullback.
6. **Stop:** candle 1's low (bullish) / high (bearish) — the
   pre-imbalance candle, not the FVG's own boundary.
7. **Target — use 3/4 of the dealing range as the optimal trade entry
   / primary target reference (OTE)**, not just CE. CE (50%) is a
   conservative first target to partial into; the far extreme of the
   range is a stretch target. A move can complete at 3/4 of the range
   and never return to the starting consolidation — don't assume it has
   to revisit the midpoint on the way. (See the open question above —
   one source disagrees with using a fixed target at all.)
8. **Pick one entry mechanism and stick with it for the setup.** Don't
   require order blocks, breakers, model 2022, COT, and seasonality to
   all agree simultaneously, and don't hop between them mid-session when
   your chosen one hasn't fired yet. If your model didn't trigger, the
   answer is to do nothing, not to switch models. Whatever PD array
   you're using, it only matters when it sits at a genuine key level at
   the right time (section 1's generalized key-level-timing principle) —
   the same array away from a key level, or outside a window where
   liquidity is actually being sought, is noise.

---

## 1. Definitions

**Dealing range:** the high and low that bound the price action you're
currently analyzing — a recent swing high to swing low, a session's
range, or a suspension block. Same underlying idea as "suspension block"
and "session range" elsewhere in this doc, generalized: whatever range
you've defined, everything else (quadrant grading, OTE, liquidity
targets) is read relative to it.

**Internal range liquidity (IRL) vs. external range liquidity (ERL)
(source 5's terminology):** a cleaner name for a distinction already
implicit above. IRL is any level sitting *inside* a defined range between
a swing high and a swing low — mainly FVGs/PD arrays. ERL is the range's
own highs and lows — the boundary itself. The stated rule: price is
always moving from one pool of liquidity to the next, specifically from
IRL to ERL — it trades into an internal FVG, then continues out to the
external range high/low, then that becomes internal to the next range,
and so on. This doesn't add new mechanics beyond what's in this doc; it's
useful mainly as shorthand for describing which kind of level a given
target is.

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

**Formal 3-candle swing high/low definition (sources 7/8):** a concrete
rule for what counts as a swing point, rather than an eyeballed pivot.
A swing high is a candle whose high is greater than the high of the
candle immediately before it and the candle immediately after it (the
mirror rule, lows lower on both sides, defines a swing low). This is the
same 3-candle shape used elsewhere in this doc (the FVG, the inside day)
applied specifically to pivot identification — use it whenever "the
recent swing high/low" needs to resolve to one specific bar rather than
a general area, e.g. for the dealing range boundary in section 0 step 1
or the daily workflow in section 6.

**Inside day (sources 7/8), two variants:** a single-candle compression
signal read as a precursor to an expansion/release move, distinct from
the multi-candle dealing-range/suspension-block containment ideas
elsewhere in this doc. **Classic variant:** today's entire range (high
and low, wick to wick) is fully contained inside yesterday's range.
**Body-only variant (a looser, source-stated simplification):** only
today's candle *body* (open/close) needs to sit inside yesterday's range;
the wicks may extend beyond it. Both variants are read the same way:
compression like this tends to resolve with an expansion move away from
the inside day's range, and the direction of that expansion is read
using the same liquidity-draw/displacement logic as the rest of this
doc, not guessed independently. Treat the body-only variant as the more
permissive, more frequently triggering version — expect it to fire more
often and be somewhat less decisive than the classic variant.

**High-resistance vs. low-resistance liquidity run (sources 7/8):** a
named classifier for how easily price is moving toward a liquidity
target, read from the structure it has to pass through on the way, not
from a single indicator. A **low-resistance run** has little opposing
structure (few stacked highs/lows, order blocks, or PD arrays) between
current price and the target — expect a fast, clean move. A
**high-resistance run** has to fight through multiple stacked levels on
the way — expect a choppier, slower move that may stall or reverse
before reaching the target, even with the right directional bias. This
is a context/regime read to apply before committing size or expecting a
clean move, not a standalone entry trigger — pair it with the decision
hierarchy and displacement requirement already in this doc.

**Rejection block (sources 7/8):** a new PD-array/re-entry reference
type, distinct from the order block and the FVG already defined here.
Formed when price wicks aggressively through a level (sweeping
liquidity) but the body closes back on the other side of it, leaving a
cluster of large, rejecting wicks at that level. The high (for a
bearish rejection) or low (for a bullish rejection) of that wick cluster
becomes the reference: price returning to it is read the same way as a
return to an order block or FVG — a re-entry opportunity in the
direction of the original rejection, not a signal to fade it.

**Sharp-reversal entry via stop/market order (sources 7/8):** a named
alternative entry mechanism to the standard "wait for the FVG pullback"
entry in section 0/this section's FVG entry mechanics. When a reversal
off a liquidity sweep is sharp and fast enough that waiting for price to
return to a PD array risks missing the move entirely (it never pulls
back), entering directly on a stop or market order in the reversal's
direction is the stated alternative — accepting a worse average price in
exchange for not missing a move that doesn't offer a retracement. Use
this as a deliberate substitution for the standard pullback entry when
conditions call for it (a low-resistance run, a clear sweep-and-reverse),
not as a default replacement for it.

**Judas swing (sources 7/8):** the named term for the pre-open
liquidity-sweep fakeout already implicit in this doc's pre-market
characterization (see Session vs. kill zone below). Price sweeps one
side's liquidity (often in the pre-market or just before a session's
main move) to trap traders into the wrong-side breakout, then reverses
hard in the opposite direction for the "real" move. This is the same
mechanic as the pre-market liquidity-seeking window already documented,
now given its standard ICT name — use the swept side as the tell for
which direction the real run is likely to go, same as before.

**Generalized key-level-timing principle (sources 7/8):** a principle
stated across multiple PD arrays in this doc rather than tied to one —
made explicit here because it governs all of them. A PD array (FVG,
order block, rejection block, BSG) only matters when it sits at a
genuine key level (a dealing-range boundary, a session high/low, a
swept liquidity pool) *and* the timing is right (a kill zone, a session
open, a moment liquidity is actually being sought) — the same array
sitting in the middle of a range, or appearing outside a window when
liquidity is being actively sought, carries materially less weight.
This generalizes the key-level gate already stated narrowly for change
in the state of delivery below, and is the same logic behind scoping SMT
divergence to liquidity-run moments (see SMT divergence below) — apply
it as a filter on every PD array in this doc, not just the ones it was
originally stated for.

**Break in structure gap (BSG) and inflection point (IP) (source 5's
terminology, IP is that source's own coined term, not ICT's):** not
every FVG carries the same probability. A BSG is an FVG that displaces
through *multiple* prior highs/lows at once, not just one — it shows
aggressive, uncontested pressure through an entire stretch of structure
where smart money had repeated chances to take the other side and
didn't. That makes it higher-probability than an ordinary single-level
FVG. Within a BSG, the inflection point is a specific sub-level: roughly
where the trapped side of the market (traders who bought/sold the
breakout, or who moved stops to breakeven as the move ran) has the
heaviest concentration of orders. Price returning to a BSG tends to react
first at the inflection point rather than needing to reach the full
FVG edge or CE. Treat this as a refinement of the plain FVG entry rule
for large, structure-breaking gaps specifically, not a replacement for
it.

**Body-vs-wick respect test (source 6):** a concrete pass/fail check for
whether a zone (FVG, order block) is actually holding once price has
traded back into it, distinct from the entry mechanics above. A wick is
allowed to pierce into the far half of the zone — that alone doesn't
invalidate it. What matters is the candle **body**: if the body stays on
the entry side of the zone's midpoint (the near half), the zone is still
read as valid and holding. If a candle *closes* its body past the
midpoint, that's the tell the zone has failed and shouldn't be trusted
for that setup anymore. Use this after entry, as an ongoing validity
check, not as an entry trigger itself.

**Immediate rebalance (source 6):** a specific, strong signal distinct
from the general "gaps as magnets" idea below. When a candle opens
inside an unfilled FVG/inefficiency and its very next close fully fills
that gap in one pass (rather than partially filling it or taking several
candles), that fast, complete fill is read as the strongest available
tell that the market is about to move hard in the fill direction —
either immediately on that same candle, or on the very next one. This is
a standalone directional signal, not just confirmation that the gap
"got revisited" the way gaps-as-magnets describes.

**Counterparty theory (source 6, naming the mechanism behind buy-side/
sell-side liquidity already described elsewhere in this doc):** smart
money needs a counterparty to fill a large position, and resting stop
orders at old highs/lows (breakout buy-stops above a high, breakout/
protective sell-stops below a low) are exactly that counterparty. Smart
money sells into buy-stops being triggered above an old high (using that
triggered buying as their fill), or buys into sell-stops being triggered
below an old low, then aims for the inefficiency/gap between that entry
and whatever opposing liquidity pool they're actually targeting. This
doesn't change any mechanic already in this doc — it's the *why* behind
liquidity pools acting as smart-money entry zones, worth keeping as the
underlying logic statement rather than just the observed pattern.

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
the candle *before* the imbalance, not the FVG's own high/low. (See the
sharp-reversal entry above for the stated alternative when waiting for
this pullback risks missing the move.)

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
one-time target that expires. (See "immediate rebalance" above for the
specific, stronger case where the fill itself happens fast and
completely in one pass.)

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
**Tiebreaker when ambiguous (source 6):** if there are two plausible
relative-equal candidates and it's genuinely unclear which to use — say,
one pair is nearly perfectly equal while the other technically qualifies
but has a messier, higher-low complicating it — default to the cleanest,
most obviously equal pair rather than the technically-arguable one.
Either can be defensible; the point of this rule is removing the
beginner's paralysis over picking between them, not claiming one is
objectively correct.

**Session vs. kill zone:** a session is the full trading window for a
region (Asia ~7pm-5am ET, London ~2am-11am ET including its own
extension, NY 9:30am-4pm ET). A kill zone is a narrower, higher-probability
sub-window inside a session (London kill zone 2am-5am ET, NY pre-market
7am-9am ET, NY open 9:30am-10am ET). 8:30am ET (common economic-release
time) behaves like a third reset point alongside the 9:30 open, whether
or not there's actual news that day. The distinction matters because
smooth (untested) highs/lows have to be scanned across the **entire
session range**, not just within the kill zone; the kill zone is where
entries are timed, not where the liquidity search is limited to. The
pre-market window specifically is characterized (source 6) as a
liquidity-seeking window: it tends to sweep one of two candidate
liquidity pools (buy-side or sell-side) before the 9:30 open, and
whichever gets swept first is read as the tell for which pool is the
next draw once the "real run" of the day begins. **This specific pattern
has a standard name (sources 7/8): the Judas swing** — see the Judas
swing definition above; same mechanic, now named.

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

**Order block (source material's "market maker buy/sell model"; source
5 restates the same idea more simply as "price shifts delivery with
force and leaves behind a key level"):** after a relative equal high/low
is swept, the last down-close candle before the reversal through that
level becomes the order block. Price is expected to return to its CE
before continuing in the sweep's direction. (See rejection block above
for a related but distinct PD-array type, formed by rejecting wicks
rather than a pre-reversal candle body.)

**Change in the state of delivery (source 5):** a confirmation signal
independent of FVGs, read off candle bodies rather than wicks. Price
delivery describes which side the market is currently seeking (bullish
delivery = seeking highs, bearish = seeking lows). Watch how candle
bodies engulf one another: in a downtrend, down-close bodies keep
engulfing up-close bodies; a shift happens when an up-close candle
suddenly engulfs a run of down-close bodies (or the reverse for an
uptrend flipping down). Since most of a candle's volume sits in its
body, this is read as "the trapped side is now underwater" — if price
holds beyond where the bulk of the opposing side is positioned, that
side is likely to exit for breakeven or a loss, which itself becomes
pressure in the new direction. Only treated as meaningful when it occurs
at a key level (a dealing-range boundary, a session high/low, a BSG/
inflection point) — away from a key level it's noise, same caveat as
everything else in this doc (see the generalized key-level-timing
principle above, which this is a specific instance of).

**Swing failure pattern (SFP) (source 5):** a candle wicks through a
prior high or low but fails to close beyond it. Read as an earlier, more
aggressive reversal signal than waiting for full displacement through
the level — the failed close itself is the tell that the level is
acting as resistance/support rather than getting run through. Best used
as one input alongside other confirmation (an inverted FVG, a change in
the state of delivery), not alone; a single SFP is the earliest and
weakest signal in this doc, not a standalone trigger.

**SMT (smart money technique) divergence (source 5):** compare two
correlated instruments at the same swing point. If one makes a new
high/low that the other fails to confirm (e.g., one index makes a lower
low while a correlated index holds a higher low), that's read as
manipulation on the one that did make the extreme, and a signal that the
divergence direction (here, higher) is more likely correct. Needs a
genuinely correlated pair to be meaningful — equity indices with each
other, or a currency against its inverse (EUR/USD vs. DXY). From your
own universe, SPY vs. QQQ or GLD vs. SLV are the closest correlated
pairs available to test this against; it doesn't apply to a single,
uncorrelated name in isolation. **Usage caveat (refinement, sources
7/8):** this is only meaningful during moments of an actual liquidity
run or volatility injection — a session open, a kill zone, a news
release — not as a constant background check across quiet, low-volume
stretches of the day. Outside those moments, two correlated instruments
can drift apart or together for reasons that have nothing to do with a
tradable manipulation signal; scope its use to the same kind of windows
the generalized key-level-timing principle above calls for.

**Red-folder news timing (source 5):** before trading a session, check
an economic calendar (the source names ForexFactory) for high-impact
("red folder") releases that day — specifically NFP, CPI, and FOMC rate
decisions (not the FOMC minutes, which the source treats as much less
impactful). A red-folder day is read as more likely to be expansive
(bigger, more tradeable moves); a day with no red-folder news is read as
more likely to chop, which argues for sizing down. Separately, avoid
being in an open position heading into a red-folder release — a fast
enough move can blow through a stop before there's a counterparty to
fill it at the intended price (slippage), producing a loss larger than
planned. This is a process check, not a price-structure rule, and unlike
most of this doc it needs no new data — just a calendar lookup before
the session.

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
- **Prefer a break-in-structure gap (BSG) over an ordinary FVG when
  both are available:** a gap that displaced through multiple prior
  highs/lows carries more weight than one that only cleared a single
  level. When trading back into a BSG, use its inflection point as the
  primary entry reference rather than the plain near edge.
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
- **Inside day as a compression/release filter:** treat an inside day
  (classic or body-only variant — see definitions) as a signal to expect
  an expansion move away from its range, and read the expansion's
  direction with the same liquidity-draw/displacement logic as any other
  setup rather than guessing it independently.
- **Classify the liquidity run condition before sizing confidence in a
  move:** before expecting a clean, fast move to a target, check whether
  price has to pass through a high-resistance run (stacked opposing
  structure — expect chop, possible stall) or a low-resistance run
  (clean path — expect a faster move) on the way there.
- **Order block confirmation:** treat a sweep of relative equal
  highs/lows as higher probability when the far-side level is only
  slightly beyond the near side (not a clean, obvious equal-high/low),
  and require price to hold above/below the swept level's close (not
  just wick through it) before treating the order block as validated.
- **Rejection block as an additional re-entry reference:** where a sweep
  leaves a cluster of rejecting wicks rather than a clean reversal
  candle, use the rejection block (see definitions) the same way as an
  order block — a return to it is a re-entry opportunity in the
  direction of the original rejection.
- **Anchor-point wick grading:** apply the anchor-point + wick-quadrant
  refinement (see definitions) before assuming a relative-equal
  high/low fully sweeps; grade the anchor candle's wick and treat CE as
  the minimum bar for the move to show strength. When two candidates are
  ambiguous, use the smoothest-candidate tiebreaker (see definitions)
  rather than agonizing over which is "technically" correct.
- **Enter at the edge, not the midpoint:** take the FVG's near edge as
  entry; don't sit on a limit order at CE hoping for a better fill.
  Once in the zone, apply the body-vs-wick respect test as an ongoing
  validity check — a wick piercing the far half is fine, a candle body
  closing past the midpoint is the tell the zone has failed. When the
  move is too sharp to offer a pullback, use the sharp-reversal
  stop/market entry instead (see definitions) rather than forcing a wait
  that may never resolve.
- **Stop at candle 1, not the FVG boundary:** use the pre-imbalance
  candle's extreme (low for bullish, high for bearish) as the initial
  stop.
- **Target OTE (3/4), not just CE:** treat 3/4 of the dealing range as
  the primary completion reference; CE is a conservative interim target
  to partial into, not the finish line. (See the open question at the
  top of this doc — source 5 argues against a fixed target entirely.)
- **One model at a time:** commit to a single entry mechanism per setup;
  don't require every PD array to agree, and don't switch models
  mid-session when the chosen one hasn't triggered yet.
- **Every PD array needs a key level and the right timing:** apply the
  generalized key-level-timing principle (see definitions) across every
  PD array in this doc — an FVG, order block, rejection block, or BSG
  away from a genuine key level, or appearing outside a window when
  liquidity is actually being sought, carries materially less weight.
- **Watch for a Judas swing before the session's real move:** be alert
  to a pre-open (or pre-main-move) liquidity sweep designed to trap the
  wrong-side breakout before the real reversal — use the swept side as
  the tell for the likely direction of the actual move, same logic as
  the pre-market liquidity-seeking window already in this doc.
- **SFP and change-in-state-of-delivery as early confirmation:** at a
  key level, either a swing failure pattern or a change in the state of
  delivery (candle bodies flipping which side is engulfing which) can be
  used as an earlier, more aggressive confirmation than waiting for full
  displacement — best combined with each other or with an inverted FVG
  rather than relied on alone.
- **SMT divergence as directional confluence, scoped to liquidity-run
  moments:** when a genuinely correlated pair (SPY/QQQ, GLD/SLV, or
  similar) diverges at a swing point *during an actual liquidity run or
  volatility injection* (a session open, a kill zone, a news release —
  not a quiet stretch), treat the instrument that failed to confirm the
  extreme as showing the higher-probability direction, and use it as an
  added confluence alongside a graded level and displacement — not as a
  standalone signal.
- **Check red-folder news before the session:** look up NFP/CPI/FOMC
  rate-decision timing before trading; size down on days without
  red-folder news, and avoid holding into a release given slippage risk.
- **Treat a fast, complete gap-fill (immediate rebalance) as a stronger
  signal than an ordinary revisit:** when a candle opens inside an
  unfilled FVG and its very next close fully fills it in one pass, that
  is a stronger directional tell than the general gaps-as-magnets idea —
  expect the move either on that candle or the very next one.
- **Use counterparty theory to sanity-check a liquidity read:** before
  treating a swept high/low as a smart-money entry zone, confirm there's
  a real reason for it to be a counterparty (resting breakout or
  protective stops) rather than assuming every sweep qualifies.

---

## 3. Opening range half-gap rule (intraday, not buildable yet)

- Measure the regular-trading-hours opening gap: previous session's
  settlement price vs. today's 9:30am ET open.
- **Gap high/low assignment (precise rule, sources 7/8):** which side of
  the gap counts as its "high" and "low" depends on whether it's a
  premium or discount gap, not just the two raw price points. For a
  **premium gap** (today's open is above the prior close): the 9:30am
  ET open is the gap **high**, the prior session's close is the gap
  **low**. For a **discount gap** (today's open is below the prior
  close): the assignment reverses — the prior close is the gap **high**,
  the 9:30am ET open is the gap **low**. Get this assignment right before
  grading the gap into quadrants; swapping high and low inverts every
  CE/OTE level computed from it.
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
- An RTH opening range gap is a standing higher-timeframe key level even
  days later, not just intraday (source 6's own example: a gap from the
  prior Wednesday was still an active reference and got traded up into
  five sessions later) — reinforces gaps-as-magnets, not a new mechanic,
  but worth noting the persistence can span days, not just hours.
- **Blocked on the same gap as the order-flow playbook's Location and
  Confirmation steps:** `collectors/price.py` only pulls
  `TimeFrame.Day`, so there is no RTH open print to measure this against.
  Needs the same intraday collector buildout flagged in
  `orderflow_strategy_rules.yaml` (`intraday_structure.status:
  not_built`). Note: the `Volume Floor 5m` sheet added to
  `market_data.xlsx` does carry real 5-minute bars (including the RTH
  open) for a 26-ticker subset back to 2026-08-24 — that data gap may no
  longer fully apply to those names specifically, though nothing here
  reads from it yet.

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
- **Equity-scaled micro-contract sizing (source 6, discretionary futures
  scalping — parallel to, not a replacement for, the pipeline's 1%-risk/
  25%-notional formula used elsewhere):** start with one micro contract;
  add one micro per $2,500 of new equity gained, and remove one micro
  per $2,500 lost. Risk 0.25-0.5% of account per trade (not the more
  common 1%) for roughly the first two years of trading a given
  strategy, on the stated logic that under-risking early curbs the
  urge to oversize after a winning streak. This is a specific,
  concrete alternative sizing model for a small futures/micro-contract
  account; it doesn't map onto the equities rotation book's existing
  position-sizing math and isn't meant to replace it.
- **Unresolved, source 5 vs. sections 0/2 above:** source 5's own
  variant of stop-trailing goes further than "ratchet on every new
  swing" — it argues for treating the trailing stop as the only exit,
  letting a winner run past OTE/CE entirely rather than partialing at a
  predetermined level, on the stated logic (from its own trade data)
  that most winners kept running well past target. Not merged into the
  rule above; see the open question at the top of this doc.

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
| Relative equal highs/lows + anchor-point wick grading + smoothest-candidate tiebreaker | Daily bars (have) | Not built |
| Turtle soup + first-FVG extension / "model 2022" | Daily bars (have) | Not built |
| Order block after sweep | Daily bars (have) | Not built |
| Smooth vs. jagged classification | Daily bars (have) | Not built |
| Break-in-structure gap (BSG) + inflection point | Daily bars (have) | Not built |
| Internal vs. external range liquidity (IRL/ERL) naming | Daily bars (have) | Not built (terminology only, no new mechanic) |
| Change in the state of delivery (candle-body engulfing) | Daily bars (have) | Not built |
| Body-vs-wick respect test (zone-holding validity check) | Daily bars (have) | Not built |
| Immediate rebalance (fast complete gap-fill signal) | Daily bars (have) | Not built |
| Swing failure pattern (SFP) | Daily bars (have) | Not built |
| Counterparty theory | N/A - explanatory logic, not a detection rule | Documented, not a build item |
| SMT divergence (scoped to liquidity-run/volatility-injection moments) | Daily bars (have), needs a correlated pair (SPY/QQQ, GLD/SLV) | Not built |
| Red-folder news timing | External (economic calendar, e.g. ForexFactory) | Not built, not data-blocked |
| Formal 3-candle swing high/low definition | Daily bars (have) | Not built |
| Inside day (classic + body-only variants) | Daily bars (have) | Not built |
| High-resistance vs. low-resistance liquidity run condition | Daily bars (have) | Not built |
| Rejection block | Daily bars (have) | Not built |
| Sharp-reversal entry (stop/market order alternative) | Daily bars (have) | Not built (discretionary entry choice, same data as FVG entry) |
| Judas swing (named pre-open liquidity-sweep fakeout) | Daily bars (have); intraday precision benefits from the same gap below | Not built (terminology for an already-noted pattern) |
| Generalized key-level-timing principle | N/A - cross-cutting filter on other PD arrays, not its own detection | Documented, applies across build items above |
| Opening range gap high/low assignment (premium/discount) | Intraday RTH open (same gap as the half-gap rule below) | Not built, blocked (same gap) |
| Opening range half-gap rule | Intraday RTH open (missing universe-wide; partially available for a 26-ticker subset via `Volume Floor 5m`) | Not built, blocked |
| Session vs. kill-zone timing, 1H/4H swing structure | Intraday (missing) | Not built, blocked (same gap as order-flow playbook) |
| Trade management rules (incl. equity-scaled micro sizing) | N/A, discretionary | Documented, not automatable |
| Daily stop/target workflow for open positions (section 6) | Daily bars (have) + position list (missing) | Manual today, blocked on position tracking |

Everything above the half-gap section is arithmetic on data the pipeline
already collects (`collectors/price.py`, daily bars), with the exception
of SMT divergence (needs a second correlated ticker's bars, also already
collected), red-folder news timing (needs an external calendar lookup,
not a pipeline data gap at all), and counterparty theory (explanatory
logic, not something to detect in data). The half-gap rule, its gap
high/low assignment refinement, and session/kill-zone timing are blocked
on the same intraday-data gap already flagged in
`orderflow_strategy_rules.yaml`.

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
   the position's direction), using the formal 3-candle swing high/low
   definition (section 1) to pin down the exact boundary bar. Note
   whether the most recent bar is an inside day (classic or body-only)
   — if so, treat it as a compression signal and expect an expansion
   move rather than reading it as just another range bar.
3. Classify recent highs/lows in that range as smooth (untested) or
   jagged (already swept). If two candidates are ambiguous, use the
   smoothest-candidate tiebreaker (section 1).
4. Apply the decision hierarchy (section 1): was there a recent sell-side
   or buy-side stop hunt on this name, then find the first imbalance in
   the return leg, then classify the level it's heading toward as a
   single high/low or a relative-equal high/low (with anchor-point +
   wick grading if it has a wick). Check whether the path to that level
   is a high-resistance or low-resistance run (section 1) to calibrate
   how clean the move toward it is likely to be.
5. Locate the first FVG with displacement in that return leg (common-gap
   filter applies; prefer a BSG over an ordinary FVG if both are
   present). Also check for a rejection block (section 1) as an
   alternative re-entry reference if the sweep left rejecting wicks
   rather than a clean reversal candle.
6. **Stop** = candle 1's low/high of that FVG (per the direction of the
   position).
7. **Target** = CE (50%) of the dealing range as the conservative first
   target to partial into; **OTE (75%)** as the primary completion
   reference; the far extreme (full relative-equal high/low, opposite
   suspension-block boundary) as a stretch target only. (See the open
   question at the top of this doc on fixed target vs. trailing stop.)
8. If price is inside a suspension block, use the block's own quadrant
   grading for interim turning points instead of a single stop/target
   pair.
9. Once the position is at a graded zone, apply the body-vs-wick respect
   test on subsequent bars to confirm the zone (and therefore the
   stop/target read) is still holding. If the move away from the zone is
   too sharp for a clean pullback entry to ever register, note that the
   sharp-reversal entry (section 1) was the applicable mechanism instead
   of the standard pullback.

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