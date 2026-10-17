-- 조인키 형식 계약: 성분셋은 전부 소문자 + '|' 조각 경계에 공백 없이 연결.
-- 위반 행이 하나라도 나오면 테스트 실패(=`dbt build` 실패).
-- 예: 'Carbidopa|Levodopa'(대문자), 'carbidopa | levodopa'(파이프 옆 공백) → 잡힘.
--
-- 실측(2026-10-17, drug_ingredient_map.csv 195,824행): ingredient_norm에 내부 공백이
-- 있는 행이 90,608개(46.3%) — zoledronic acid·insulin glargine처럼 성분 자체 화학명이
-- 여러 단어인 경우가 흔하다. "문자열에 공백이 하나라도 있으면 FAIL"로 짰던 이전 버전은
-- 이런 정상 값까지 전부 형식 위반으로 잘못 잡았다 — '|' 조각 '안'의 공백이 아니라
-- '|' 조각 '경계'의 공백만 봐야 한다.
with parts as (
    select
        entity_id,
        resolved_ingredient_set as s,
        unnest(string_split(resolved_ingredient_set, '|')) as part
    from {{ ref('stg_resolution') }}
    where resolved_ingredient_set is not null and resolved_ingredient_set <> ''
)
select entity_id, s as resolved_ingredient_set
from parts
where s <> lower(s)        -- 전체 소문자 아님
   or part <> trim(part)    -- '|' 조각 앞뒤 공백
   or part = ''             -- 빈 조각(연속 파이프 등)
