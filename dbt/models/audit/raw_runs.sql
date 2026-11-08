-- The collector run log, newest first.
select run_id, source, dataset, status, row_count, file_count, min_date, max_date, started_at, finished_at, error
from {{ source('raw', 'manifest') }}
order by started_at desc
