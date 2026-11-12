-- 산술 불변식: 동시출현(a)은 각 가장자리(a+b, a+c)보다 클 수 없다 — "가장자리 합
-- 트릭"이 b=(a+b)-a, c=(a+c)-a로 뺄셈 유도하므로, 이게 깨지면 b나 c가 음수가 돼
-- 2×2표 자체가 성립하지 않는다. 게이트 기준이 아니라 집계가 제대로 됐는지 보는
-- 안전장치.
select ingredient, pt, a, a_plus_b, a_plus_c
from {{ ref('fct_signal_scores') }}
where a > a_plus_b or a > a_plus_c
