-- 조인키 형식 계약: fct_case_drug_reaction.ingredient_set도 stg_resolution과 같은
-- 규칙(소문자, '|' 조각 앞뒤 공백 없음)이어야 한다 — 조인해서 받아온 값이니 당연히
-- 같아야 하지만, 조인 로직이 바뀌어 형식이 깨지는 걸 바로 잡기 위해 별도로 검사한다.
--
-- 실측(2026-10-17, drug_ingredient_map.csv 195,824행): ingredient_norm에 내부 공백이
-- 있는 행이 90,608개(46.3%) — zoledronic acid·insulin glargine처럼 성분 자체 화학명이
-- 여러 단어인 경우가 흔하다. "문자열에 공백이 하나라도 있으면 FAIL"로 짰던 이전 버전은
-- 이런 정상 값까지 전부 형식 위반으로 잘못 잡는다 — '|' 조각 '안'의 공백이 아니라
-- '|' 조각 '경계'의 공백(예: "carbidopa | levodopa")만 봐야 한다.
with parts as (
    select
        caseid,
        primaryid,
        ingredient_set as s,
        unnest(string_split(ingredient_set, '|')) as part
    from {{ ref('fct_case_drug_reaction') }}
    where ingredient_set is not null and ingredient_set <> ''
)
select caseid, primaryid, s as ingredient_set
from parts
where s <> lower(s)        -- 전체 소문자 아님
   or part <> trim(part)    -- '|' 조각 앞뒤 공백
   or part = ''             -- 빈 조각(연속 파이프 등)
