-- 조인키 형식 계약(부작용): 묶음(| 포함)은 원소가 알파벳 정렬돼 있어야 한다.
-- 'ten|sjs|dress' 처럼 순서가 뒤바뀌면 같은 신호가 다른 키가 되어 정답지 대조가 깨진다.
-- 원본 문자열과, |로 쪼개 정렬해 다시 이은 문자열이 다르면 위반.
-- (원소 내부 공백은 정렬 대상 문자열에 그대로 포함 — PT 용어 특성.)
with split as (
    select
        signal_term,
        pt_set_id as s,
        unnest(string_split(pt_set_id, '|')) as part
    from {{ ref('stg_reaction_dict') }}
    where pt_set_id like '%|%'
),
resorted as (
    select
        signal_term,
        any_value(s)                         as original,
        string_agg(part, '|' order by part)  as sorted_form
    from split
    group by signal_term
)
select signal_term, original, sorted_form
from resorted
where original <> sorted_form
