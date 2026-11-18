-- fct_signal_features 중간 단계: 원본 DRUG의 dechal/rechal을 (primaryid, drug_seq)당 0/1 플래그로 접은 것.
-- 같은 행이 여러 번 있어도(원본 완전 중복) max라 결과가 같다. 문자열 원본 행을 그대로 조인하면 OOM.
{{ config(materialized='table') }}

select
    cast(primaryid as varchar)                                          as primaryid,
    try_cast(drug_seq as integer)                                       as drug_seq,
    max(case when upper(trim(dechal)) = 'Y' then 1 else 0 end)          as dechal_y,
    max(case when upper(trim(rechal)) = 'Y' then 1 else 0 end)          as rechal_y,
    max(case when upper(trim(dechal)) in ('Y', 'N') then 1 else 0 end)  as dechal_known,
    max(case when upper(trim(rechal)) in ('Y', 'N') then 1 else 0 end)  as rechal_known
from iceberg_scan('s3://boaz-1234-825494477740-ap-northeast-2-an/iceberg_warehouse/stage_a_raw/faers_drug')
group by 1, 2
