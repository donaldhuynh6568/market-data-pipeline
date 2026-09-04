# Order Flow Pipeline Build-Out Plan
**Source:** Insights from "World Champion Reveals the Orderflow Strategy" (Chris Kmer, Robbins Cup) cross-referenced against the current market-data pipeline and analysis framework.

---

## 1. The Reference Framework

Chris's process runs **Environment -> Location -> Confirmation -> Execution**, always assessed pre-market, never mid-session.

| Step | What he uses | Purpose |
|---|---|---|
| **Environment** | Higher-timeframe structure (1H/4H -- value up/down/sideways, higher-highs/higher-lows) + naive GEX regime (positive = dampening, negative = amplifying) + call wall/put wall/gamma flip zone | Sets expectations for what *kind* of day it is -- choppy/pinned vs. fast/trending |
| **Location** | Fibonacci retracement (70.5/78.8/88.6 "golden pocket") from swing low to swing high, defining discount/premium -- must sit **outside** the value area, entered only after internal structure (a swing point, ideally a liquidity sweep) forms | Defines *where* to do business -- not chasing extension |
| **Confirmation** | Footprint candles: volume-at-price + bid/ask delta. Looking for **absorption** (aggressive selling at an extreme, no downside result) followed by a **second failed push** that fails to make a new low | Live, intrabar evidence that a level is actually holding *because real size defended it* -- not just inferred after the fact |
| **Execution** | Stop below the failed-seller low; targets at POC/swing highs/psychological clusters; trail behind continued buyer aggression; move to break-even if buyers can't reclaim value area | Turns confirmation into a managed trade |

**Risk/process notes:** trades only the first ~1.5 hrs of NY open (sometimes Asia session); hard volume floor (won't trade below 20,000 contracts/5-min candle -- participation dying out); win rate ~60-65%, profit factor ~1.8, average ~1.5R; hard stop after 2 consecutive losses; one strategy only, no strategy-hopping; execution quality tracked separately from P&L.

---

## 2. Mapping Against the Current Pipeline

| His step | Pipeline equivalent | Status |
|---|---|---|
| Environment (structure + GEX regime) | GEX Data sheet (regime, walls, flip level) + swing high/low technicals | **Built** |
| Location (Fib discount/premium, outside value area) | FVG framework (unfilled gaps as pullback/entry zones) | **Built, different notation, same function** |
| Confirmation (footprint absorption + failed second push) | -- | **Not built -- the core gap** |
| Execution (stop/target/trail off confirmation) | Scenario Watch tab (daily-bar "wick and reject" read) | **Partially built, daily granularity only -- retrospective, not live** |

---

## 3. Open Design Question: GEX as Regime vs. GEX as Levels

**Important tension surfaced directly from the source material, not yet resolved:**

Chris explicitly states he does **not** trade off GEX walls as literal support/resistance: *"I'm not necessarily looking for specific levels... to take a bounce off of a put wall or a call wall, but I do want to understand the environment that we're in."* For him, GEX is a **regime/character forecast only** (expect choppy vs. expect trending), not a level to fade or target.

The current workbook (Confluence Report) treats call/put walls as **actual price levels** -- QQQ $725 as resistance, SPY $772 as a pin, etc. -- layered directly against FVG price-action zones.

**This is not necessarily wrong** -- the FVG-confluence approach gives independent justification for treating those zones as real, separate from the GEX label. But it's worth being explicit going forward about which mode is in use:
- **Mode A (regime only):** GEX tells you what kind of day/week to expect; price-action tools (FVG, swing structure) do the actual level-calling
- **Mode B (levels):** GEX walls themselves are treated as tradeable support/resistance

**Action item:** decide and document which mode the framework is actually using per-ticker, since QQQ's "put wall" was already shown to sometimes be an ATM artifact rather than a structural level (see Gamma Wall Explainer tab) -- a case where Mode B produces a misleading level and Mode A (regime-only) would have avoided the error entirely.

---

## 4. The Flow-Data Gap -- What's Actually Missing

None of the current sheets capture buyer-initiated daily/intraday flow directly:

| Sheet | Captures | Does NOT capture |
|---|---|---|
| Price Data (OHLCV) | Total volume | Buy/sell attribution |
| Options Data / Unusual Activity | EOD vol/OI snapshot | Aggressor side, intraday timing |
| GEX Data | Dealer gamma positioning (derived) | Real-time transacting flow |
| COT Data | Managed-money futures positioning | Daily granularity (weekly-lagged), equities |
| 13F Holdings | Institutional stock holdings | Daily granularity (quarterly, 45-day lagged) |
| Insider Trades | Corporate insider transactions | Institutional/big-money flow (different population) |

**What Chris's "confirmation" step actually requires, translated into data terms:**
1. **Tick-level trade data with buy/sell classification** (Lee-Ready algorithm) -- already on the pipeline roadmap (Alpaca SIP trade-and-quote feed)
2. **Block trade / large-print detection** -- filtered from the tick feed by size threshold + venue (off-exchange prints via FINRA ADF/TRF tags)
3. **Real-time options sweeps/blocks** -- aggressor-tagged, multi-exchange options prints; requires a third-party feed (e.g., Unusual Whales, Cheddar Flow, FlowAlgo) since Alpaca's standard options endpoints don't provide aggressor-side attribution

---

## 5. Build-Out Roadmap (Prioritized)

### Phase 1 -- Tick Classification (fits existing stack)
- Confirm Alpaca subscription tier includes SIP-level tick data (not just IEX -- often a paid add-on)
- Pull trades/quotes for QQQ, SPY, SLV via `alpaca-py`
- Implement Lee-Ready classification (compare trade price to prevailing bid/ask)
- Aggregate to daily net buy/sell volume as a first proxy signal
- **Stretch:** build footprint-candle-equivalent (volume + delta per price level, per bar) for the NY-open window specifically, mirroring Chris's actual tool

### Phase 2 -- Block/Large-Print Flagging
- Filter tick feed for single-print size thresholds relative to trailing average
- Tag off-exchange venue codes (FINRA ADF/TRF) as a rough "dark pool print" flag
- Cheap add-on once Phase 1 infrastructure exists

### Phase 3 -- Options Sweep/Block Feed (requires new vendor)
- Evaluate Unusual Whales / Cheddar Flow / FlowAlgo for aggressor-tagged options prints
- This is the closest analogue to Chris's live orderflow confirmation, applied to the options market instead of futures/equities
- Highest signal value, but a genuinely new data source and cost -- not buildable from existing Alpaca access

### Already on the existing roadmap (from prior sessions, still relevant)
- Intraday (minute-level) bars via `collectors/price.py` modifications -- needed regardless, since Phase 1 confirmation logic requires intraday granularity
- Tick-level trade classification for the closing auction window specifically (separate use case from NY-open confirmation, but same underlying infrastructure)

---

## 6. Immediate Next Steps

1. Resolve the Mode A vs. Mode B GEX question (Section 3) -- affects how existing tabs should be labeled/used going forward
2. Confirm Alpaca data tier supports SIP tick data before scoping Phase 1 build
3. Scope the Lee-Ready classification script (Phase 1) as the next concrete build task
4. Revisit Phase 3 vendor evaluation once Phase 1/2 are validated and the value of aggressor-tagged flow vs. self-built proxies is clearer
