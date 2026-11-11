-- Candidate table — 약이름당 여러 행(후보 이력). entity_id로 Resolution과 연결.
-- stg_resolution.sql과 같은 이유로 seed 대신 build_resolution.py 실데이터 산출물을 읽는다.
{{ config(materialized='table') }}

select
    cast(entity_id                         as varchar) as entity_id,
    cast(raw_drug_name                     as varchar) as raw_drug_name,
    cast(normalized_drug_name              as varchar) as normalized_drug_name,
    cast(nullif(stage, '')                 as integer) as stage,
    cast(nullif(candidate_rank, '')        as integer) as candidate_rank,
    cast(candidate_alias                   as varchar) as candidate_alias,
    cast(candidate_ingredient_set          as varchar) as candidate_ingredient_set,
    cast(unii                              as varchar) as unii,
    cast(nullif(lexical_score, '')         as double)  as lexical_score,
    cast(nullif(semantic_score, '')        as double)  as semantic_score,
    cast(source                            as varchar) as source
from read_csv_auto('../../silver/drug_dict/candidate_merged.csv', all_varchar=true)
