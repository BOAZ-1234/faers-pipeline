-- SILVER-2 "펼치기" 그레인 — 신고(case) 1건의 약물 1개 × 반응 1개가 한 행.
-- role_cod 필터(PS/SS만)·caseversion 최신판 선택·중복 ingredient_set 처리는
-- 전부 GOLD 집계 시점에 적용한다 — 여기선 원본을 그대로 보존해, 나중에 필터
-- 기준을 바꿔 재실험하거나 시점(as_of)별로 다른 버전을 골라 쓸 수 있게 한다
-- (B단계 그레인 결정 1~4, action-plan.md 1장 참고).
--
-- 명시적 cast로 contract가 선언한 data_type과 정확히 일치시킨다(seed 추론 타입 오차 방지).
{{ config(materialized='table') }}

select
    cast(caseid        as varchar) as caseid,
    cast(primaryid     as varchar) as primaryid,
    cast(caseversion   as integer) as caseversion,
    cast(fda_dt         as varchar) as fda_dt,
    cast(role_cod       as varchar) as role_cod,
    cast(ingredient_set as varchar) as ingredient_set,
    cast(unii           as varchar) as unii,
    cast(pt              as varchar) as pt
from {{ ref('case_drug_reaction_sample') }}
