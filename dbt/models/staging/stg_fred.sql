-- One row per FRED series and observation date, newest ingest wins, with the date the value became usable.
-- FRED dates are period starts, so a monthly print is not known on its observation date: available_at adds a
-- release lag by frequency (config/strategy.yaml fred_lag_days). Frequency is inferred from observation spacing.
-- Values are the latest vintage, not point-in-time revisions: fine for VIXCLS (never revised), not for GDP.
{% set lags = var('fred_lag_days') %}

with obs as (
    select
        series_id,
        cast(date as date) as observation_date,
        cast(value as double) as value,
        ingested_at
    from {{ source('raw', 'fred_series') }}
    qualify row_number() over (partition by series_id, cast(date as date) order by ingested_at desc, run_id desc) = 1
),

spacing as (
    select series_id, median(gap_days) as median_gap_days
    from (
        select
            series_id,
            date_diff('day', lag(observation_date) over (partition by series_id order by observation_date), observation_date) as gap_days
        from obs
    )
    where gap_days is not null
    group by series_id
),

classified as (
    select
        series_id,
        case
            when median_gap_days <= 4 then 'daily'
            when median_gap_days <= 10 then 'weekly'
            when median_gap_days <= 45 then 'monthly'
            else 'quarterly'
        end as frequency
    from spacing
)

select
    o.series_id,
    o.observation_date,
    o.value,
    c.frequency,
    cast(o.observation_date + to_days(
        case c.frequency
            when 'daily' then {{ lags['daily'] }}
            when 'weekly' then {{ lags['weekly'] }}
            when 'monthly' then {{ lags['monthly'] }}
            else {{ lags['quarterly'] }}
        end
    ) as date) as available_at,
    o.ingested_at
from obs o
join classified c using (series_id)
where o.value is not null
