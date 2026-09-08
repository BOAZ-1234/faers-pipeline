-- 조인키 존재 계약: '확정'/'수기'로 판정됐다면 반드시 성분셋이 있어야 한다.
-- (미해결/보류는 성분이 없을 수 있으므로 제외.)
-- 확정인데 성분셋이 비면 → B·D 조인에서 그 약이 통째로 사라짐 → 실패로 잡는다.
select
    entity_id,
    resolution_status,
    resolved_ingredient_set
from {{ ref('stg_resolution') }}
where resolution_status in ('확정', '수기')
  and (resolved_ingredient_set is null or resolved_ingredient_set = '')
