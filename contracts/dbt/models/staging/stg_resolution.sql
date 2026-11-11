-- Resolution table — 약이름 1개당 1행. B(채점)·D(서빙) 조인의 성분키 원천.
--
-- silver/drug_dict/build_resolution.py가 Stage1(사전+prod_ai)·Stage2(유사도)·
-- BRAND_DOSE·OUT_OF_SCOPE·Stage3(LLM)을 합쳐 만든 실데이터 CSV를 읽는다(seed가
-- 아니라 read_csv_auto인 이유: seeds/는 이 프로젝트 관례상 손으로 만든 장난감
-- 픽스처용 — dbt_project.yml 참고 — 이지만 이건 실제 파이프라인 산출물이다).
-- 전부 varchar로 읽은 뒤 NULLIF+cast — 미해결/OOS 행은 resolved_stage·confidence가
-- 빈 문자열이라 바로 integer/double로 캐스팅하면 깨진다.
{{ config(materialized='table') }}

select
    cast(entity_id                        as varchar) as entity_id,
    cast(raw_drug_name                    as varchar) as raw_drug_name,
    cast(resolution_status                as varchar) as resolution_status,
    cast(resolved_ingredient_set          as varchar) as resolved_ingredient_set,
    cast(unii                             as varchar) as unii,
    cast(nullif(resolved_stage, '')       as integer) as resolved_stage,
    cast(resolved_method                  as varchar) as resolved_method,
    cast(nullif(confidence, '')           as double)  as confidence,
    cast(pipeline_version                 as varchar) as pipeline_version,
    cast(dictionary_version               as varchar) as dictionary_version
from read_csv_auto('../../silver/drug_dict/resolution_merged.csv', all_varchar=true)
