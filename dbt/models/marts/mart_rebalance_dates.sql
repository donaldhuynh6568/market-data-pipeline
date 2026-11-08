-- Grain: rebalance_date. Monthly schedule: the signal is read at the close of the last trading day of each month
-- (rebalance_date), filled execution_lag_days trading days later (entry_date), and held until the next entry.
-- The newest rebalance has no exit yet: it is the current portfolio, excluded from performance.
-- A month end is a trading day whose next trading day is in a new month. The latest date in the calendar is
-- never one: the current month has not finished, so its last day is not known yet.
with calendar as (
    select date, trading_day_index, lead(date) over (order by date) as next_date
    from {{ ref('stg_trading_calendar') }}
),

month_ends as (
    select date as rebalance_date, trading_day_index
    from calendar
    where next_date is not null and month(next_date) != month(date)
),

scheduled as (
    select
        m.rebalance_date,
        e.date as entry_date
    from month_ends m
    left join {{ ref('stg_trading_calendar') }} e
        on e.trading_day_index = m.trading_day_index + {{ var('execution_lag_days') }}
)

-- Inner join: only rebalances inside the usable range, so every signal has the features it needs.
select
    s.rebalance_date,
    s.entry_date,
    lead(s.entry_date) over (order by s.rebalance_date) as exit_date,
    sp.split
from scheduled s
join {{ ref('mart_split') }} sp on sp.date = s.rebalance_date
