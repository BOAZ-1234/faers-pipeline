-- 조인키 형식 계약: 복합제 성분셋은 알파벳 정렬돼 있어야 한다.
-- 'levodopa|carbidopa' 처럼 순서가 뒤바뀌면 같은 약이 다른 키가 되어 조인이 깨진다.
-- 원본 문자열과, |로 쪼개 정렬해 다시 이은 문자열이 다르면 위반.
with split as (
    select
        entity_id,
        resolved_ingredient_set as s,
        unnest(string_split(resolved_ingredient_set, '|')) as part
    from {{ ref('stg_resolution') }}
    where resolved_ingredient_set like '%|%'
),
resorted as (
    select
        entity_id,
        any_value(s)                         as original,
        string_agg(part, '|' order by part)  as sorted_form
    from split
    group by entity_id
)
select entity_id, original, sorted_form
from resorted
where original <> sorted_form
