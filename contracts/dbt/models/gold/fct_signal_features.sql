-- GOLD 랭킹 피처 — D단계 v3~v5 재료(백테스트 인수인계 §3, scoring/ranking.py VERSIONS).
-- 성분×부작용 조합 1개당 1행, fct_signal_scores와 같은 (ingredient, pt) 키로 붙는다.
--
-- 세는 기준은 fct_signal_scores와 **완전히 같다** — 그래야 피처와 IC025가 같은 신고 집단을
-- 가리킨다(assert_features_align_with_scores가 a 일치로 강제):
--   · caseid별 최신 caseversion만 · role_cod PS/SS만 · ingredient_set을 성분 단위로 쪼갬 · 고유 caseid
-- 그래서 피처의 분모(이 조합이 함께 나온 신고 수)는 점수표의 a와 같다.
--
-- 피처(전부 0~1 비율, 분모 = a):
--   death_frac   (v3) 결과(OUTC)에 DE(사망)가 있는 신고 비율
--   serious_frac (참고) DE·LT·HO·DS·CA·RI(중대 결과) 중 하나라도 있는 비율 — OT(기타)는 제외
--   dechal_frac  (v4) 이 성분의 PS/SS 약물 행에 dechal='Y'(중단 후 호전)가 있는 신고 비율
--   rechal_frac  (v4) 같은 조건으로 rechal='Y'(재투여 후 재발)
--   recency      (v5) 데이터 마지막 접수일(fda_dt 최댓값) 기준 최근 2년 안에 접수된 신고 비율
--
-- dechal/rechal은 비어 있는 게 많아(기획서 4-7: 값이 없다고 "아니다"가 아님) Y만 세고,
-- 값이 Y/N으로 채워진 신고 수(dechal_known_n, rechal_known_n)를 따로 남긴다.
-- recency 기준일을 "오늘"이 아니라 데이터 최댓값으로 둔다 — 재실행해도 값이 같아야
-- 재현성이 성립한다(시점 재현 v2에서 as_of별로 다시 계산할 때도 같은 규칙).
--
-- OUTC는 2026-11-17 적재(stage_a_raw.faers_outc, PR#77). 신고(primaryid)에 결과가 여러 개면
-- 여러 행이라 신고별로 묶어서 센다. 결과 행이 아예 없는 신고는 사망·중대 아님(0)으로 센다.
--
-- 계산은 int_signal_* 중간 표로 나눠 저장한다 — 617M행 원본에서 한 쿼리로 돌리면 서버(16GB)에서
-- 임시파일 폭증·OOM이 났다(2026-11-18 실측). 원본은 바로 작은 단위(신고 / 신고×성분 / 신고×부작용)로
-- 줄여 저장하고, 여기선 그 셋을 caseid로 붙여 (ingredient, pt)별로 센다.
{{ config(materialized='table') }}

with ref_date as (
    select max(fda_date) as max_fda_date from {{ ref('int_signal_case') }}
)

select
    cast(cd.ingredient                                          as varchar) as ingredient,
    cast(cr.pt                                                  as varchar) as pt,
    cast(count(*)                                               as integer) as a,
    cast(avg(ci.is_death)                                       as double)  as death_frac,
    cast(avg(ci.is_serious)                                     as double)  as serious_frac,
    cast(avg(cd.dechal_y)                                       as double)  as dechal_frac,
    cast(avg(cd.rechal_y)                                       as double)  as rechal_frac,
    cast(sum(cd.dechal_known)                                   as integer) as dechal_known_n,
    cast(sum(cd.rechal_known)                                   as integer) as rechal_known_n,
    cast(avg(case when ci.fda_date >= rd.max_fda_date - interval 2 year then 1 else 0 end)
                                                                as double)  as recency
from {{ ref('int_signal_case_drug') }} cd
join {{ ref('int_signal_case_reac') }} cr on cd.caseid = cr.caseid
join {{ ref('int_signal_case') }} ci on cd.caseid = ci.caseid
cross join ref_date rd
group by cd.ingredient, cr.pt
