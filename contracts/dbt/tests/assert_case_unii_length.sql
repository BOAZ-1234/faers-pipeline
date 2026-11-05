-- 조인키 형식 계약: UNII는 정확히 10자, 또는 빈값(복합제 묶음은 UNII 없음).
select
    caseid,
    primaryid,
    unii,
    length(unii) as unii_len
from {{ ref('fct_case_drug_reaction') }}
where unii <> '' and length(unii) <> 10
