-- Resolution table — 약이름 1개당 1행. B(채점)·D(서빙) 조인의 성분키 원천.
-- 명시적 cast로 contract가 선언한 data_type과 정확히 일치시킨다(seed 추론 타입 오차 방지).
{{ config(materialized='table') }}

select
    cast(entity_id               as varchar) as entity_id,
    cast(raw_drug_name           as varchar) as raw_drug_name,
    cast(resolution_status       as varchar) as resolution_status,
    cast(resolved_ingredient_set as varchar) as resolved_ingredient_set,
    cast(unii                    as varchar) as unii,
    cast(resolved_stage          as integer) as resolved_stage,
    cast(resolved_method         as varchar) as resolved_method,
    cast(confidence              as double)  as confidence,
    cast(pipeline_version        as varchar) as pipeline_version,
    cast(dictionary_version      as varchar) as dictionary_version
from {{ ref('resolution_sample') }}
