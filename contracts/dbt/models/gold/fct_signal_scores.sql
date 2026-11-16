-- GOLD "4칸 집계" — 성분×부작용 조합마다 2×2표(a,a+b,a+c,N)와 IC025(+ROR·PRR)를
-- 계산한다. project-plan.html §4-7("게이트/필터/랭킹 3단")에 따라 게이트(IC025>0,
-- a≥3)·필터(시점 라벨)·랭킹은 전부 D단계 몫이다 — 여기선 계산된 값을 전부 내보내고
-- 거르지 않는다(v1 범위: project-plan.html §4-7 "v1 = IC025 단독, 파이프라인 완주
-- 목적"). B단계 그레인 결정(action-plan.md 1장) 적용:
--   · role_cod — PS/SS만 "노출"로 센다(a, a+b 쪽에만 적용 — a+c·N은 약과 무관해서
--     role_cod와 상관없음, 아니면 2×2표 네 칸이 서로 다른 모집단을 가리키게 됨)
--   · caseversion — caseid별 전역 최신판만(전량 집계, as_of 재현은 세부2 몫)
--   · 중복 — COUNT(*)가 아니라 COUNT(DISTINCT caseid)
--   · ingredient_set은 성분 단위로 쪼갠다(복합제 그대로 두면 같은 성분이 여러 조합으로
--     흩어져 통계적 힘이 약해짐 — 표준 disproportionality 관례, 대신 상호작용 자체를
--     못 구분하는 손실은 솔로/복합제 case 수를 같이 내보내 사람이 참고하게 한다. 조합
--     단위 상호작용 전용 통계(Omega 등)는 v1 이후 작업)
{{ config(materialized='table') }}

with latest_version as (
    select caseid, max(caseversion) as caseversion
    from {{ ref('fct_case_drug_reaction') }}
    group by caseid
),
latest_rows as (
    select f.*
    from {{ ref('fct_case_drug_reaction') }} f
    join latest_version lv
      on f.caseid = lv.caseid and f.caseversion = lv.caseversion
),
case_drug as (
    select distinct
        caseid,
        trim(ing) as ingredient,
        (len(string_split(ingredient_set, '|')) > 1) as is_combo
    from latest_rows, unnest(string_split(ingredient_set, '|')) as t(ing)
    where role_cod in ('PS', 'SS') and ingredient_set <> ''
),
case_reac as (
    select distinct caseid, pt
    from latest_rows
    where pt <> ''
),
n_total as (
    select count(distinct caseid) as n from latest_rows
),
drug_totals as (
    select ingredient, count(distinct caseid) as a_plus_b
    from case_drug
    group by ingredient
),
combo_stats as (
    select
        ingredient,
        count(distinct case when not is_combo then caseid end) as solo_case_count,
        count(distinct case when is_combo then caseid end) as combo_case_count
    from case_drug
    group by ingredient
),
reac_totals as (
    select pt, count(distinct caseid) as a_plus_c
    from case_reac
    group by pt
),
co_occur as (
    select cd.ingredient, cr.pt, count(distinct cd.caseid) as a
    from case_drug cd
    join case_reac cr on cd.caseid = cr.caseid
    group by cd.ingredient, cr.pt
)

select
    cast(co.ingredient                                     as varchar) as ingredient,
    cast(co.pt                                              as varchar) as pt,
    cast(co.a                                               as integer) as a,
    cast(dt.a_plus_b                                        as integer) as a_plus_b,
    cast(rt.a_plus_c                                        as integer) as a_plus_c,
    cast(n.n                                                as integer) as n,
    cast(
        log2((co.a + 0.5) / ((dt.a_plus_b * rt.a_plus_c * 1.0 / n.n) + 0.5))
        as double
    ) as ic,
    cast(
        log2((co.a + 0.5) / ((dt.a_plus_b * rt.a_plus_c * 1.0 / n.n) + 0.5))
        - 3.3 * power(co.a + 0.5, -0.5)
        - 2   * power(co.a + 0.5, -1.5)
        as double
    ) as ic025,
    cast(
        (co.a * 1.0 * (n.n - dt.a_plus_b - rt.a_plus_c + co.a))
        / ((dt.a_plus_b - co.a) * (rt.a_plus_c - co.a))
        as double
    ) as ror,
    cast(
        (co.a * 1.0 / dt.a_plus_b) / ((rt.a_plus_c - co.a) * 1.0 / (n.n - dt.a_plus_b))
        as double
    ) as prr,
    cast(cs.solo_case_count                                 as integer) as solo_case_count,
    cast(cs.combo_case_count                                as integer) as combo_case_count
from co_occur co
join drug_totals dt on co.ingredient = dt.ingredient
join reac_totals rt on co.pt = rt.pt
join combo_stats cs on co.ingredient = cs.ingredient
cross join n_total n
