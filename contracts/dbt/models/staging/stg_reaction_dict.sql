-- 부작용 사전 — 정답지 signal 1개당 1행. D(채점)에서 '사람이 쓴 정답지 부작용문'을
-- 표준 용어 묶음(pt_set_id)으로 맞출 때 쓰는 조인키 원천.
-- (FAERS 부작용은 이미 표준 PT라 여기서 안 다룬다 — reaction_pt 원형 그대로 B로 간다.)
--
-- 상류 reaction_dict 는 signal/pt_set 로 내보내므로, 여기서 계획서 키
-- signal_term/pt_set_id 로 '통일'(alias)한다. 실제 source 연결 시에도 이 alias 유지.
{{ config(materialized='table') }}

select
    cast(signal as varchar) as signal_term,   -- 계획서 §5 키. 상류 컬럼 signal
    cast(pt_set as varchar) as pt_set_id,      -- 계획서 §5-1 키. 상류 컬럼 pt_set
    cast(method as varchar) as method,
    cast(status as varchar) as status
from {{ ref('reaction_dict_sample') }}
