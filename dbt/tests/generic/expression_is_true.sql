{# Every row satisfies the expression (rows where it is false or null fail). #}
{% test expression_is_true(model, expression, where_clause=none) %}
select *
from {{ model }}
where not coalesce(({{ expression }}), false)
{% if where_clause %}and ({{ where_clause }}){% endif %}
{% endtest %}
