-- The trading calendar is SPY's bar dates. Every mart shares this one date axis, so crypto (which trades on
-- weekends) is sampled on equity trading days.
select
    date,
    row_number() over (order by date) as trading_day_index,
    row_number() over (partition by year(date), month(date) order by date) as day_of_month_index,
    row_number() over (partition by year(date), month(date) order by date desc) as days_to_month_end
from {{ ref('stg_price_daily') }}
where ticker = 'SPY'
