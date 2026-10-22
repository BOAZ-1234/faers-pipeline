-- 형식 계약: fda_dt는 FAERS 원본 그대로 YYYYMMDD 8자리 숫자 문자열이어야 한다.
-- 실측(2026-10-07) faers_demo.fda_dt 표본 기준 — 이 형식이 깨지면 시점 재현(§4-6)의
-- as_of 비교(문자열/날짜 캐스팅)가 조용히 틀어진다.
select
    caseid,
    primaryid,
    fda_dt
from {{ ref('fct_case_drug_reaction') }}
where length(fda_dt) <> 8
   or fda_dt !~ '^[0-9]{8}$'
