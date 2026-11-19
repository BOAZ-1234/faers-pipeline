-- fct_signal_features 중간 단계: 최신판 신고 1건당 결과(OUTC)·접수일.
-- OUTC는 신고(primaryid)에 결과가 여러 개면 여러 행이라 신고별로 묶는다. 결과 행이 없는 신고는
-- 사망·중대 아님(0). 중대 = DE·LT·HO·DS·CA·RI(OT 제외).
{{ config(materialized='table') }}

with latest_case as (
    select distinct f.caseid, f.primaryid, f.fda_dt
    from {{ ref('fct_case_drug_reaction') }} f
    join {{ ref('int_signal_latest_version') }} lv
      on f.caseid = lv.caseid and f.caseversion = lv.caseversion
),
outc as (
    select
        cast(primaryid as varchar) as primaryid,
        max(case when upper(trim(outc_cod)) = 'DE' then 1 else 0 end) as is_death,
        max(case when upper(trim(outc_cod)) in ('DE', 'LT', 'HO', 'DS', 'CA', 'RI') then 1 else 0 end) as is_serious
    from iceberg_scan('s3://boaz-1234-825494477740-ap-northeast-2-an/iceberg_warehouse/stage_a_raw/faers_outc')
    group by 1
)

select
    c.caseid,
    max(coalesce(o.is_death, 0))             as is_death,
    max(coalesce(o.is_serious, 0))           as is_serious,
    max(try_strptime(c.fda_dt, '%Y%m%d'))    as fda_date
from latest_case c
left join outc o on c.primaryid = o.primaryid
group by c.caseid
