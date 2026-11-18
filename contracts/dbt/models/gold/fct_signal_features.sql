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
{{ config(materialized='table') }}

-- 메모리: 617M행 원본을 최신판 전체 컬럼(latest_rows f.*)으로 한 번 뽑아 세 곳에서 다시 쓰면
-- DuckDB가 그 중간 결과를 통째로 디스크에 써서(서버 실측 15분에 임시파일 55GB) 터진다. 그래서
-- 원본은 필요한 컬럼만 뽑아 곧바로 작은 단위(신고 / 신고×약물 / 신고×부작용)로 줄이고,
-- 신고 1건을 가리키는 표만 여러 번 쓴다. OUTC·DRUG 원본도 DISTINCT 없이 max로 묶는다
-- (같은 행이 여러 번 있어도 max는 같아서 결과가 같다).
with latest_version as (
    select caseid, max(caseversion) as caseversion
    from {{ ref('fct_case_drug_reaction') }}
    group by caseid
),
-- 최신판 신고 1건당 접수일
latest_case as (
    select distinct f.caseid, f.primaryid, f.fda_dt
    from {{ ref('fct_case_drug_reaction') }} f
    join latest_version lv
      on f.caseid = lv.caseid and f.caseversion = lv.caseversion
),
-- 최신판 신고의 PS/SS 약물 행(부작용 차원을 뺀 것)
latest_drug as (
    select distinct f.caseid, f.primaryid, f.drug_seq, f.ingredient_set
    from {{ ref('fct_case_drug_reaction') }} f
    join latest_version lv
      on f.caseid = lv.caseid and f.caseversion = lv.caseversion
    where f.role_cod in ('PS', 'SS') and f.ingredient_set <> ''
),
case_reac as (
    select distinct f.caseid, f.pt
    from {{ ref('fct_case_drug_reaction') }} f
    join latest_version lv
      on f.caseid = lv.caseid and f.caseversion = lv.caseversion
    where f.pt <> ''
),
-- 원본 OUTC·DRUG는 키 단위로 미리 0/1 플래그로 접어서 조인한다 — 문자열 원본 행을 그대로
-- 조인하면 조인 해시 테이블이 메모리 상한(8GB)을 넘는다(서버 실측 OOM).
outc as (
    select
        cast(primaryid as varchar) as primaryid,
        max(case when upper(trim(outc_cod)) = 'DE' then 1 else 0 end) as is_death,
        max(case when upper(trim(outc_cod)) in ('DE', 'LT', 'HO', 'DS', 'CA', 'RI') then 1 else 0 end) as is_serious
    from iceberg_scan('s3://boaz-1234-825494477740-ap-northeast-2-an/iceberg_warehouse/stage_a_raw/faers_outc')
    group by 1
),
drug_chal as (
    select
        cast(primaryid as varchar) as primaryid,
        try_cast(drug_seq as integer) as drug_seq,
        max(case when upper(trim(dechal)) = 'Y' then 1 else 0 end) as dechal_y,
        max(case when upper(trim(rechal)) = 'Y' then 1 else 0 end) as rechal_y,
        max(case when upper(trim(dechal)) in ('Y', 'N') then 1 else 0 end) as dechal_known,
        max(case when upper(trim(rechal)) in ('Y', 'N') then 1 else 0 end) as rechal_known
    from iceberg_scan('s3://boaz-1234-825494477740-ap-northeast-2-an/iceberg_warehouse/stage_a_raw/faers_drug')
    group by 1, 2
),
-- 신고(최신판) 1건당 결과·접수일
case_info as (
    select
        c.caseid,
        max(coalesce(o.is_death, 0)) as is_death,
        max(coalesce(o.is_serious, 0)) as is_serious,
        max(try_strptime(c.fda_dt, '%Y%m%d')) as fda_date
    from latest_case c
    left join outc o on c.primaryid = o.primaryid
    group by c.caseid
),
ref_date as (
    select max(fda_date) as max_fda_date from case_info
),
-- 신고 × 성분: 같은 신고에 같은 성분 약물 행이 여럿이면 하나라도 Y면 Y
case_drug as (
    select
        ld.caseid,
        trim(ing) as ingredient,
        max(coalesce(dc.dechal_y, 0)) as dechal_y,
        max(coalesce(dc.rechal_y, 0)) as rechal_y,
        max(coalesce(dc.dechal_known, 0)) as dechal_known,
        max(coalesce(dc.rechal_known, 0)) as rechal_known
    from latest_drug ld
    cross join unnest(string_split(ld.ingredient_set, '|')) as t(ing)
    left join drug_chal dc
      on ld.primaryid = dc.primaryid and ld.drug_seq = dc.drug_seq
    group by ld.caseid, trim(ing)
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
from case_drug cd
join case_reac cr on cd.caseid = cr.caseid
join case_info ci on cd.caseid = ci.caseid
cross join ref_date rd
group by cd.ingredient, cr.pt
