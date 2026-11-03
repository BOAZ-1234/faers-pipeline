-- 계산 정합성 계약: 모든 행에서 a+b+c+d = n_total 이어야 한다(가장자리 합
-- 트릭의 뺄셈 유도가 맞았다는 기본 불변식). 위반 행이 하나라도 있으면 FAIL.
select
    ingredient_set,
    pt,
    a, b, c, d, n_total,
    (a + b + c + d) as cell_sum
from {{ ref('fct_signal_scores') }}
where (a + b + c + d) <> n_total
