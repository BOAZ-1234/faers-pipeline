-- 형식 계약: 비율 피처는 0~1, 알려진 건수는 0~a. 벗어나면 조인 fan-out(같은 신고를 여러 번 셈)이 의심된다.
select ingredient, pt, a, death_frac, serious_frac, dechal_frac, rechal_frac, recency,
       dechal_known_n, rechal_known_n
from {{ ref('fct_signal_features') }}
where death_frac   not between 0 and 1
   or serious_frac not between 0 and 1
   or dechal_frac  not between 0 and 1
   or rechal_frac  not between 0 and 1
   or recency      not between 0 and 1
   or death_frac > serious_frac
   or dechal_known_n not between 0 and a
   or rechal_known_n not between 0 and a
