-- Grain: split, quintile. The gradient test: mean forward return per momentum quintile at each rebalance
-- (quintile 1 = strongest momentum). A working signal shows returns falling from 1 to 5; a top quintile that
-- lags quintile 2 is the "fading winners" warning. Only the splits in report_splits are shown, so the test
-- split stays unseen until the strategy is frozen.
with ranked as (
    select
        f.ticker,
        r.rebalance_date,
        r.split,
        ntile(5) over (partition by r.rebalance_date order by f.mom_score desc, f.ticker) as quintile,
        t.fwd_ret
    from {{ ref('mart_features') }} f
    join {{ ref('mart_rebalance_dates') }} r on r.rebalance_date = f.date
    join {{ ref('mart_targets') }} t on t.ticker = f.ticker and t.date = f.date
    where f.is_rankable and f.mom_score is not null
),

per_rebalance as (
    select split, rebalance_date, quintile, avg(fwd_ret) as mean_fwd_ret
    from ranked
    where fwd_ret is not null
    group by split, rebalance_date, quintile
)

select
    split,
    quintile,
    count(*) as n_rebalances,
    avg(mean_fwd_ret) as mean_fwd_ret,
    median(mean_fwd_ret) as median_fwd_ret,
    avg(case when mean_fwd_ret > 0 then 1.0 else 0.0 end) as share_positive
from per_rebalance
where split in ({{ "'" ~ var('report_splits') | join("', '") ~ "'" }})
group by split, quintile
order by split, quintile
