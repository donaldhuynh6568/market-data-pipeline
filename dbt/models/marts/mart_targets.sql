-- Grain: ticker, date. Forward returns: TARGETS ONLY. No feature or signal model may ref() this table
-- (tests/test_dbt_project.py enforces it); evaluation joins it, nothing learns from it.
{% set h = var('target_horizon') %}

with fwd as (
    select
        ticker,
        date,
        is_rankable,
        {{ forward_return('close', h, 'ticker', 'date') }} as fwd_ret
    from {{ ref('mart_price_daily') }}
),

ranked as (
    select
        ticker,
        date,
        rank() over (partition by date order by fwd_ret desc) as fwd_rank,
        fwd_ret > median(fwd_ret) over (partition by date) as fwd_above_median
    from fwd
    where is_rankable and fwd_ret is not null
)

select
    f.ticker,
    f.date,
    {{ h }} as horizon,
    f.fwd_ret,
    r.fwd_rank,
    r.fwd_above_median
from fwd f
left join ranked r using (ticker, date)
