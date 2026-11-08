-- Grain: variant, ticker, rebalance_date. Target weights per strategy variant; every candidate and baseline
-- lands here so one query compares them. Random-selection baselines are added by the simulation (they need
-- seeded draws), with the same eligible set used here.
--
-- Eligible = rankable (not cash-like) with a full 3-month momentum score. That is also the minimum-history
-- rule: a new listing joins the universe once it has 64 trading days of bars.
{% set trend = var('trend_filter') %}

with eligible as (
    select
        f.ticker,
        f.date as rebalance_date,
        f.mom_score,
        coalesce(f.trend_risk_on, true) as trend_risk_on,
        -- Ticker breaks ties so top-N always holds exactly N names.
        row_number() over (partition by f.date order by f.mom_score desc, f.ticker) as score_rank,
        count(*) over (partition by f.date) as n_eligible
    from {{ ref('mart_features') }} f
    join {{ ref('mart_rebalance_dates') }} r on r.rebalance_date = f.date
    where f.is_rankable and f.mom_score is not null
),

{% for n in var('top_n_grid') %}
momentum_top{{ n }} as (
    select
        'momentum_top{{ n }}' as variant, ticker, rebalance_date, mom_score as score, score_rank,
        score_rank <= {{ n }} as hold,
        case when score_rank <= {{ n }} then 1.0 / {{ n }} else 0.0 end as weight
    from eligible
),

momentum_top{{ n }}_trend as (
    -- Risk-on: identical to momentum_top{{ n }}. Risk-off (every trend index below its 200-day MA): all in
    -- the defensive asset.
    select
        'momentum_top{{ n }}_trend' as variant, ticker, rebalance_date, mom_score as score, score_rank,
        case when trend_risk_on then score_rank <= {{ n }} else ticker = '{{ trend["defensive_asset"] }}' end as hold,
        case
            when trend_risk_on then (case when score_rank <= {{ n }} then 1.0 / {{ n }} else 0.0 end)
            else (case when ticker = '{{ trend["defensive_asset"] }}' then 1.0 else 0.0 end)
        end as weight
    from eligible
),
{% endfor %}

equal_weight as (
    select 'equal_weight' as variant, ticker, rebalance_date, mom_score as score, score_rank,
        true as hold, 1.0 / n_eligible as weight
    from eligible
),

spy_bh as (
    select 'spy_bh' as variant, 'SPY' as ticker, rebalance_date,
        cast(null as double) as score, cast(null as bigint) as score_rank, true as hold, 1.0 as weight
    from {{ ref('mart_rebalance_dates') }}
)

{% for n in var('top_n_grid') %}
select * from momentum_top{{ n }}
union all
select * from momentum_top{{ n }}_trend
union all
{% endfor %}
select * from equal_weight
union all
select * from spy_bh
