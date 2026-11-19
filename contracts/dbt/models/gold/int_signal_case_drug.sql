-- fct_signal_features 중간 단계: 최신판 신고 × 성분(PS/SS만, ingredient_set을 성분 단위로 쪼갬 —
-- fct_signal_scores의 case_drug와 같은 기준). 같은 신고에 같은 성분 약물 행이 여럿이면 하나라도 Y면 Y.
{{ config(materialized='table') }}

with latest_drug as (
    select distinct f.caseid, f.primaryid, f.drug_seq, f.ingredient_set
    from {{ ref('fct_case_drug_reaction') }} f
    join {{ ref('int_signal_latest_version') }} lv
      on f.caseid = lv.caseid and f.caseversion = lv.caseversion
    where f.role_cod in ('PS', 'SS') and f.ingredient_set <> ''
)

select
    ld.caseid,
    trim(ing)                          as ingredient,
    max(coalesce(dc.dechal_y, 0))      as dechal_y,
    max(coalesce(dc.rechal_y, 0))      as rechal_y,
    max(coalesce(dc.dechal_known, 0))  as dechal_known,
    max(coalesce(dc.rechal_known, 0))  as rechal_known
from latest_drug ld
cross join unnest(string_split(ld.ingredient_set, '|')) as t(ing)
left join {{ ref('int_signal_drug_flags') }} dc
  on ld.primaryid = dc.primaryid and ld.drug_seq = dc.drug_seq
group by ld.caseid, trim(ing)
