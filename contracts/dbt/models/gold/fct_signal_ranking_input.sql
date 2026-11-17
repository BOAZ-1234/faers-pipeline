-- D단계 백테스트 입력(scoring.backtest --signals) — fct_signal_scores에 v3~v5 피처를 붙인 것.
-- fct_signal_scores(이수연)를 고치지 않고 별도 모델로 합친다 — v2 시점 스냅샷 작업과 같은 파일을
-- 동시에 건드리지 않게. 키는 (ingredient, pt), 두 모델의 a가 같은지는 assert_features_align_with_scores가 강제.
-- 피처가 없는 조합은 없어야 하지만(같은 기준으로 셈) left join으로 두어 점수표 행이 빠지지 않게 한다.
--
-- export(예, contracts/dbt에서):
--   duckdb dev.duckdb "COPY (SELECT * FROM fct_signal_ranking_input) TO 'fct_signal_scores_v5.parquet' (FORMAT parquet)"
{{ config(materialized='table') }}

select
    s.*,
    f.death_frac,
    f.serious_frac,
    f.dechal_frac,
    f.rechal_frac,
    f.recency,
    f.dechal_known_n,
    f.rechal_known_n
from {{ ref('fct_signal_scores') }} s
left join {{ ref('fct_signal_features') }} f
  on s.ingredient = f.ingredient and s.pt = f.pt
