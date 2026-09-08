-- 조인키 형식 계약: UNII는 정확히 10자, 또는 빈값(복합제 묶음은 UNII 없음).
-- 9자/11자 같은 잘림·오타를 잡는다. 위반 행이 나오면 실패.
select
    entity_id,
    unii,
    length(unii) as unii_len
from {{ ref('stg_resolution') }}
where unii <> '' and length(unii) <> 10
