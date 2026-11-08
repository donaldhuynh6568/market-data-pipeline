{# Join-cardinality guard: the model has exactly as many rows as the model it was built from. #}
{% test equal_row_count(model, compare_model) %}
select a.n as model_rows, b.n as compare_rows
from (select count(*) as n from {{ model }}) a
cross join (select count(*) as n from {{ compare_model }}) b
where a.n != b.n
{% endtest %}
