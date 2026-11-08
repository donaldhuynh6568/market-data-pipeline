-- Grain: ticker, date (daily; signals sample it at rebalance dates). Only returns, ratios, ranks and flags: no
-- price levels, and nothing from mart_targets. Windows count trading-day rows and stay null until full.
-- Every column is registered in config/feature_registry.yaml (checked by market_data_pipeline.build).
{% set trend = var('trend_filter') %}

with p as (
    select * from {{ ref('mart_price_daily') }}
),

windows as (
    select
        ticker,
        asset_class,
        is_rankable,
        date,
        ret_1d,
        close / lag(close, 21) over w - 1 as ret_21,
        close / lag(close, 63) over w - 1 as ret_63,
        close / lag(close, 126) over w - 1 as ret_126,
        close / lag(close, 252) over w - 1 as ret_252,
        lag(close, 21) over w / lag(close, 252) over w - 1 as ret_252_skip21,
        case when count(ret_1d) over w21 = 21 then stddev_samp(ret_1d) over w21 * sqrt(252) end as vol_21,
        case when count(ret_1d) over w63 = 63 then stddev_samp(ret_1d) over w63 * sqrt(252) end as vol_63,
        case when count(close) over w200 = 200 then close / avg(close) over w200 - 1 end as dist_ma200
    from p
    window
        w as (partition by ticker order by date),
        w21 as (partition by ticker order by date rows between 20 preceding and current row),
        w63 as (partition by ticker order by date rows between 62 preceding and current row),
        w200 as (partition by ticker order by date rows between 199 preceding and current row)
),

scored as (
    select
        *,
        -- 3-month vol-adjusted momentum (rotationstrategy.md: "simple momentum with volatility penalty").
        ret_63 / nullif(vol_63, 0) as mom_score,
        dist_ma200 > 0 as above_ma200
    from windows
),

ranks as (
    select ticker, date, rank() over (partition by date order by mom_score desc) as mom_rank
    from scored
    where is_rankable and mom_score is not null
),

{% set index_list = "'" ~ trend['indices'] | join("', '") ~ "'" %}
market_trend as (
    select
        date,
        bool_or(case when ticker = 'SPY' then above_ma200 end) as spy_above_ma200,
        bool_or(case when ticker = 'QQQ' then above_ma200 end) as qqq_above_ma200,
        -- Risk-on unless every trend index is below its 200-day MA (null until all have a full MA window).
        case
            when count(case when ticker in ({{ index_list }}) then above_ma200 end) = {{ trend['indices'] | length }}
            then bool_or(case when ticker in ({{ index_list }}) then above_ma200 end)
        end as trend_risk_on
    from scored
    group by date
),

vix as (
    select available_at, observation_date, value
    from {{ ref('stg_fred') }}
    where series_id = 'VIXCLS'
),

joined as (
    select
        s.*,
        r.mom_rank,
        m.spy_above_ma200,
        m.qqq_above_ma200,
        m.trend_risk_on,
        v.value as vix,
        date_diff('day', v.observation_date, s.date) as vix_days_since_update,
        cast(v.available_at as date) as vix_available_at
    from scored s
    left join ranks r using (ticker, date)
    left join market_trend m using (date)
    -- Point in time: the latest VIX print already available on this date, never one published later.
    asof left join vix v on s.date >= v.available_at
)

select
    j.ticker,
    j.date,
    j.asset_class,
    j.is_rankable,
    j.ret_1d,
    j.ret_21,
    j.ret_63,
    j.ret_126,
    j.ret_252,
    j.ret_252_skip21,
    j.vol_21,
    j.vol_63,
    j.mom_score,
    j.mom_rank,
    j.dist_ma200,
    j.above_ma200,
    j.spy_above_ma200,
    j.qqq_above_ma200,
    j.trend_risk_on,
    j.vix,
    j.vix_days_since_update,
    j.vix_available_at,
    month(j.date) as month,
    (cal.day_of_month_index <= 3 or cal.days_to_month_end = 1) as turn_of_month
from joined j
join {{ ref('stg_trading_calendar') }} cal using (date)
