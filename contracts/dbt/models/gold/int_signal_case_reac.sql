-- fct_signal_features 중간 단계: 최신판 신고 × 부작용(pt) — fct_signal_scores의 case_reac와 같은 기준.
{{ config(materialized='table') }}

select distinct f.caseid, f.pt
from {{ ref('fct_case_drug_reaction') }} f
join {{ ref('int_signal_latest_version') }} lv
  on f.caseid = lv.caseid and f.caseversion = lv.caseversion
where f.pt <> ''
