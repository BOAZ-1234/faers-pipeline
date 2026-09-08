-- 조인키 형식 계약: 성분셋은 전부 소문자 + 공백 없이 '|' 로만 연결.
-- 위반 행이 하나라도 나오면 테스트 실패(=`dbt build` 실패).
-- 예: 'Carbidopa|Levodopa'(대문자), 'carbidopa | levodopa'(공백) → 잡힘.
select
    entity_id,
    resolved_ingredient_set
from {{ ref('stg_resolution') }}
where resolved_ingredient_set <> lower(resolved_ingredient_set)   -- 소문자 아님
   or resolved_ingredient_set like '% %'                          -- 공백 포함
