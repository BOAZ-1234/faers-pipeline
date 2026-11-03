-- GOLD — 4칸 집계 + IC025 (project-plan.html §4-5)
--
-- 조합마다 안 훑고, 전체를 3번만 스캔해서 가장자리 합(a, a+b, a+c, N)만 구하고
-- 나머지(b, c, d)는 뺄셈으로 유도한다 — 조합 수와 무관하게 비용이 고정된다.
--
-- 모집단(N) 정의: B단계 그레인 결정(1장, b_stage/schema.yml) 그대로 적용한다 —
-- role_cod는 PS/SS만("노출"로 인정하는 약), 미해결 약물명(ingredient_set 빈값)은
-- 제외, case당 최신 caseversion만. 이 필터를 a/a+b/a+c/N 네 값 전부에 동일하게
-- 적용해야 2×2표가 내부적으로 일관된다 — 한쪽만 다르게 필터링하면 예를 들어
-- "약은 못 셌는데 반응은 셌다" 같은 불일치가 생긴다.
--
-- 최신 caseversion 판단은 전체(미필터) 테이블 기준이어야 한다 — 어떤 case의
-- 최신 버전 행이 전부 role_cod=C/I뿐이라고 해서 그 이전 버전으로 잘못 fallback
-- 하면 안 되기 때문(그런 경우는 해당 case가 이번 분석 모집단에서 그냥 빠진다).

{{ config(materialized='table') }}

with latest_version as (
    select caseid, max(caseversion) as max_version
    from {{ ref('fct_case_drug_reaction') }}
    group by caseid
),

filtered as (
    select f.caseid, f.ingredient_set, f.pt
    from {{ ref('fct_case_drug_reaction') }} f
    inner join latest_version lv
        on f.caseid = lv.caseid and f.caseversion = lv.max_version
    where f.role_cod in ('PS', 'SS')
      and f.ingredient_set is not null
      and f.ingredient_set <> ''
),

-- 전체 스캔 ① — 약물×반응 동시출현 (a)
pair_counts as (
    select ingredient_set, pt, count(distinct caseid) as a
    from filtered
    group by ingredient_set, pt
),

-- 전체 스캔 ② — 약물별 총 신고수 (a+b)
drug_totals as (
    select ingredient_set, count(distinct caseid) as drug_total
    from filtered
    group by ingredient_set
),

-- 전체 스캔 ③ — 반응별 총 신고수 (a+c)
reaction_totals as (
    select pt, count(distinct caseid) as reaction_total
    from filtered
    group by pt
),

-- N — 전체 신고수 (위 필터가 적용된 모집단 기준)
totals as (
    select count(distinct caseid) as n_total
    from filtered
),

cells as (
    select
        p.ingredient_set,
        p.pt,
        p.a,
        dt.drug_total - p.a as b,
        rt.reaction_total - p.a as c,
        t.n_total - dt.drug_total - rt.reaction_total + p.a as d,
        t.n_total,
        cast(dt.drug_total as double) * rt.reaction_total / t.n_total as expected_count
    from pair_counts p
    join drug_totals dt on dt.ingredient_set = p.ingredient_set
    join reaction_totals rt on rt.pt = p.pt
    cross join totals t
)

select
    ingredient_set,
    pt,
    a,
    b,
    c,
    d,
    n_total,
    expected_count,
    ln((a + 0.5) / (expected_count + 0.5)) / ln(2) as ic,
    ln((a + 0.5) / (expected_count + 0.5)) / ln(2)
        - 3.3 * pow(a + 0.5, -0.5)
        - 2 * pow(a + 0.5, -1.5) as ic025,
    -- ROR·PRR — 계산 비용 0이라 같이 저장. 판정·정렬은 IC025 하나만 쓴다(§4-5,
    -- 전부 같은 2×2표의 변형 관계라 여러 개 쓰는 건 중복). 연속성 보정 없음 —
    -- b=0 또는 d=0이면 그대로 무한/NULL이 나올 수 있다(ROR 자체의 알려진 한계).
    (cast(a as double) / b) / (cast(c as double) / d) as ror,
    (cast(a as double) / (a + b)) / (cast(c as double) / (c + d)) as prr
from cells
