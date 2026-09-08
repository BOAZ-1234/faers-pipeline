-- 조인키 형식 계약(부작용): pt_set_id 는 전부 소문자여야 한다.
-- 성분셋(ingredient_set)과 달리 PT 용어는 내부 공백을 가지므로('pancreatitis acute')
-- 공백은 허용한다 — 구분자는 '|', 각 원소는 소문자 표준 용어.
-- 위반 행이 하나라도 나오면 테스트 실패(=`dbt build` 실패).
select
    signal_term,
    pt_set_id
from {{ ref('stg_reaction_dict') }}
where pt_set_id <> lower(pt_set_id)   -- 소문자 아님
