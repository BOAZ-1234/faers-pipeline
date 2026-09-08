-- Candidate table — 약이름당 여러 행(후보 이력). entity_id로 Resolution과 연결.
{{ config(materialized='table') }}

select
    cast(entity_id                 as varchar) as entity_id,
    cast(raw_drug_name             as varchar) as raw_drug_name,
    cast(normalized_drug_name      as varchar) as normalized_drug_name,
    cast(stage                     as integer) as stage,
    cast(candidate_rank            as integer) as candidate_rank,
    cast(candidate_alias           as varchar) as candidate_alias,
    cast(candidate_ingredient_set  as varchar) as candidate_ingredient_set,
    cast(unii                      as varchar) as unii,
    cast(lexical_score             as double)  as lexical_score,
    cast(semantic_score            as double)  as semantic_score,
    cast(source                    as varchar) as source
from {{ ref('candidate_sample') }}
