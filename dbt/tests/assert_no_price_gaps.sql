-- An equity missing a bar on a trading day inside its own coverage window is a data gap, not a holiday.
-- Warn rather than fail: a halted or newly listed name can legitimately miss a day, and the price mart caps the fill.
{{ config(severity = 'warn') }}

with coverage as (
    select ticker, min(date) as first_date, max(date) as last_date
    from {{ ref('stg_price_daily') }}
    where asset_class = 'equity'
    group by ticker
)

select c.ticker, cal.date
from coverage c
join {{ ref('stg_trading_calendar') }} cal on cal.date between c.first_date and c.last_date
left join {{ ref('stg_price_daily') }} p on p.ticker = c.ticker and p.date = cal.date
where p.ticker is null
