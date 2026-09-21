# Custom Stock Rotation Strategy Analysis Summary

## Target Implementation Overview

**Custom Basket Strategy**: Momentum-based rotation between 25 selected assets including individual stocks, ETFs, and crypto, applying lessons from successful ETF rotation strategies.

**Target Assets**: Mixed portfolio of growth stocks, international exposure, defensive assets, and alternative investments
- **Growth Tech**: META, PLTR, NVDA, MSFT, TSM, AAPL, AMZN, GOOGL, PANW, NFLX
- **Consumer/Services**: CAVA, COST, DAL, SPOT, HOOD
- **International**: BABA, FXI, ASML, CQQQ
- **Traditional Value**: AXP
- **ETF Exposure**: QQQ
- **Alternative Assets**: GLD, BTC, ETH, COIN

**Strategy Goal**: Apply proven rotation methodology to high-growth individual stock universe while maintaining risk management principles.

## Custom Basket Analysis & Challenges

### Asset Categories & Characteristics
**High Volatility Growth (70% of basket)**
- Individual tech stocks: META, PLTR, NVDA, MSFT, TSM, AAPL, AMZN, GOOGL, PANW, NFLX
- Higher volatility than ETFs but potentially higher momentum signals
- Correlation risk during market downturns

**International Exposure (16%)**
- China focus: BABA, FXI, CQQQ
- European: ASML  
- Different economic cycles but still equity-correlated

**Defensive/Alternative Assets (14%)**
- Traditional safe haven: GLD
- Crypto exposure: BTC, ETH, COIN
- Note: Crypto may not provide traditional defensive properties

### Key Differences from Original ETF Strategy
**Advantages:**
- Higher momentum potential in individual stocks
- More concentrated bets on conviction plays
- Access to high-growth sectors underrepresented in broad ETFs

**Challenges:**
- Much higher volatility than Grossmann's ETF basket
- Reduced diversification across asset classes
- Limited true "defensive" assets (only GLD as traditional safe haven)
- Individual stock risk vs. broad market exposure
- Crypto correlation increasingly positive with tech stocks

## Expected Performance & Risk Profile

### Realistic Expectations vs Original Strategies
**Potential Advantages:**
- Higher momentum signals from individual stocks
- Access to high-growth narratives (AI, fintech, etc.)
- Less institutional competition than ETF rotation

**Expected Challenges:**
- Higher volatility (30-50% vs 25% in original strategy)
- More frequent rotations needed due to stock volatility
- Limited defensive options may hurt bear market performance
- Concentration risk in tech/growth sectors

### Risk-Adjusted Targets
**Conservative Estimate:**
- Target CAGR: 15-25% (vs original 41.4%)
- Expected Volatility: 35-45%
- Sharpe Ratio Target: 0.4-0.7 (vs original 1.38)

**Key Risk Factors:**
- Tech sector concentration during growth-to-value rotations
- Crypto correlation breakdown during stress
- Limited true diversification compared to global ETF approach

## Implementation Strategy for Custom Basket

### Phase 1: Basic Momentum Rotation
- **Basket**: Full 25-asset universe as provided
- **Method**: 3-month lookback, monthly rotation  
- **Ranking**: Simple momentum with volatility penalty
- **Position**: Single asset concentration (100% in winner)

### Phase 2: Risk Management Layer
- **Trend Filter**: 200-day MA on QQQ (tech proxy) and SPY
- **Defensive Trigger**: Rotate to GLD when both indices below MA
- **Volatility Scaling**: Reduce position size for high-vol individual stocks
- **Max Drawdown Stop**: Exit to cash if portfolio down >20% from peak

### Phase 3: Advanced Optimization
- **Multi-Asset Positions**: Hold top 2-3 performers with weight allocation
- **Dynamic Lookback**: Shorter periods (1-2 months) for high-momentum environments
- **Sector Awareness**: Limit concentration in single sectors
- **Crypto Integration**: Treat BTC/ETH as separate momentum category

### Monitoring & Adaptation Requirements
**Weekly Review:**
- Check for major momentum shifts requiring early rotation
- Monitor correlation breakdowns between tech stocks
- Assess defensive asset behavior

**Monthly Execution:**
- Full momentum ranking and position updates
- Rebalance based on volatility-adjusted signals
- Document rotation rationale for strategy improvement

## Key Insights & Improvements

### Seasonality Patterns
- Strategy outperforms at turn of quarter
- **Statistically significant**: Turn of month performance (day 0 and +2 days)
- Decline 2 days after month turn suggests potential for daily rotation

### Risk Management Enhancement
- Adding 200-day moving average trend filter improved performance:
  - CAGR: 10.12% → 11.52%
  - Sharpe: 0.49 → 0.72
  - Max Drawdown: -52.82% → -22.42%
  - Time in market: 100% → 71.28%

## Key Technical Insights

### Grossmann's Optimization Method
- **MATLAB-based parameter search**: Tests lookback (1 week to 12 months) vs investment period (2 weeks to 3 months)
- **3D mountain diagrams**: Visualize optimal parameter combinations
- **ETF suitability test**: Good rotation ETFs show stable positive returns across parameter ranges
- **Volatility adjustment**: Critical for mixed-volatility baskets (EDV vs equity ETFs)

### Why Commodities Fail
- Too volatile and easily manipulated
- No stable parameter combinations for consistent returns
- Random noise overwhelms trend signals

### Critical Success Factors
- **Investment period < lookback period**: Essential relationship
- **Longer timeframes**: Reduce random market noise impact
- **Stable trends**: ETFs must show persistent momentum characteristics
- **Volume requirements**: Prevent manipulation in shorter timeframes

## Expert Commentary Highlights (Scott Allen)

### Advanced Improvements
1. **Frequency**: Weekly/daily checking reduces timing luck
2. **Sector Selection**: Only need 5 least correlated sectors (XLE, XLU, XLP, XLC, XLV)
3. **Gold Inclusion**: Always include gold - doesn't correlate with anything
4. **Better Momentum Metrics**: 
   - Use EMA of median prices vs simple close
   - Consider acceleration vs just momentum
   - Factor in volatility (Sharpe ratio approach)
5. **Endpoint Smoothing**: Avoid randomness from start/end dates

### Why It Works
- **Behavioral Alpha**: Investors overextrapolate trends
- **Feature-Based**: Captures broader macro trends vs individual asset risk
- **Regime Adaptive**: Automatically adjusts without explicit regime detection
- **30+ Year Track Record**: Proven persistence across market cycles

## Implementation Strategy Recommendations

### Option 1: Original GMR Approach (Historical)
- **Basket**: MDY, IEV, EEM, ILF, EPP, EDV, SHY
- **Method**: 3-month lookback, monthly rotation
- **Risk**: May not work in current market regime

### Option 2: Optimized Broad Basket (Recommended)
- **Basket**: SPY, QQQ, IWM, EFA, EEM, TLT, SHV
- **Method**: Monthly rotation with 3-month lookback
- **Enhancement**: 200-day MA trend filter for risk management

### Option 3: Advanced Implementation
- **Frequency**: Weekly/daily checking with monthly execution
- **Basket**: Optimized based on correlation analysis
- **Metrics**: Enhanced momentum with volatility adjustment
- **Additions**: Gold (GLD) for diversification

### Risk Considerations
- **Market Regime Sensitivity**: Original strategy failed post-2015
- **Transaction Costs**: Higher frequency increases costs
- **Overfitting Risk**: Too many parameters reduce robustness
- **Correlation Changes**: Monitor defensive asset correlations

## Conclusion & Strategic Takeaways

### What Works (Validated Principles)
- **Momentum rotation**: Fundamental behavioral alpha source persists
- **Monthly frequency**: Good balance of signal vs noise
- **Defensive assets**: Essential for drawdown protection (EDV/TLT performance in 2008)
- **Global diversification**: Different economic cycles provide rotation opportunities
- **Risk-adjusted metrics**: Sharpe ratio improvements justify complexity

### What's Changed (Post-2015 Challenges)
- **Original basket decay**: GMR performance degraded significantly
- **Market efficiency**: Increased competition may have reduced alpha
- **Regime changes**: Different correlation patterns and market dynamics

### Implementation Priority
1. **Start simple**: Test broad basket with basic 3-month momentum
2. **Add defensive layer**: Include trend filter and treasury allocation
3. **Monitor performance**: Track when rotation stops working
4. **Evolve basket**: Adapt to changing market conditions

### Final Assessment
The ETF rotation strategy demonstrates robust theoretical foundation with:
- **Historical validation**: 10+ years of outperformance data
- **Risk management**: Built-in crash protection through defensive rotation
- **Behavioral persistence**: Momentum effects unlikely to disappear completely
- **Adaptability**: Framework allows for basket evolution and optimization

**Key Risk**: Strategy performance is highly dependent on basket selection and market regime - requires ongoing monitoring and potential adaptation.