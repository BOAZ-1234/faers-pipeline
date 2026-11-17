-- 정합성 계약: 피처(fct_signal_features)는 점수표(fct_signal_scores)와 같은 신고 집단을 세야 한다.
-- 두 모델의 키(ingredient, pt) 집합과 동시출현 a가 한 행이라도 다르면 실패 — 최신판·PS/SS·성분 분해
-- 기준 중 하나가 어긋났다는 뜻이고, 그러면 death_frac 등이 IC025와 다른 모집단의 비율이 된다.
select coalesce(s.ingredient, f.ingredient) as ingredient,
       coalesce(s.pt, f.pt) as pt,
       s.a as score_a,
       f.a as feature_a
from {{ ref('fct_signal_scores') }} s
full outer join {{ ref('fct_signal_features') }} f
  on s.ingredient = f.ingredient and s.pt = f.pt
where s.a is null or f.a is null or s.a <> f.a
