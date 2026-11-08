-- Grain: ticker, date. The clean daily spine every other mart builds on.
-- Spine = trading calendar x universe, from each ticker's first bar onward. A missing bar takes the last close
-- (ASOF join) for at most ffill_cap_days calendar days; beyond that close is null, never interpolated.
-- Crypto lands on the same equity trading dates, so its ret_1d over a Monday includes the weekend.
with universe as (
    select * from {{ ref('universe') }}
),

first_bar as (
    select ticker, min(date) as first_date from {{ ref('stg_price_daily') }} group by ticker
),

spine as (
    select u.ticker, u.asset_class, u.category, u.is_cash_like, u.is_rankable, cal.date
    from universe u
    join first_bar f using (ticker)
    join {{ ref('stg_trading_calendar') }} cal on cal.date >= f.first_date
),

filled as (
    select
        s.*,
        p.date as price_date,
        p.close as last_close,
        p.volume
    from spine s
    asof left join {{ ref('stg_price_daily') }} p
        on s.ticker = p.ticker and s.date >= p.date
),

capped as (
    select
        ticker,
        asset_class,
        category,
        is_cash_like,
        is_rankable,
        date,
        price_date,
        date_diff('day', price_date, date) as days_since_update,
        price_date < date as is_filled,
        case when date_diff('day', price_date, date) <= {{ var('ffill_cap_days') }} then last_close end as close,
        case when price_date = date then volume end as volume
    from filled
)

select
    *,
    close / lag(close) over (partition by ticker order by date) - 1 as ret_1d
from capped
