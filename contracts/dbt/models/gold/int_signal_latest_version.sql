-- fct_signal_features 중간 단계: caseid별 최신 caseversion — fct_signal_scores의 latest_version과 같은 정의.
-- 피처 계산을 한 쿼리로 돌리면 서버(메모리 16GB)에서 OOM이 나서, 단계마다 표로 저장해 나눠 계산한다.
{{ config(materialized='table') }}

select caseid, max(caseversion) as caseversion
from {{ ref('fct_case_drug_reaction') }}
group by caseid
