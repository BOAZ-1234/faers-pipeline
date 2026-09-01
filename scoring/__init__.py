"""채점기 (scoring) — 우리 순위표를 FDA 정답지로 채점한다.

핵심 계약(계획서 노트 ③):
  순위표와 정답지는 **같은 키**로 만든다. 정규화(브랜드→성분, 부작용 표기→PT)는
  이 패키지 밖(이수연 drug_dict / 하경 reaction_dict)에서 끝내서 넘기고,
  채점기는 모두가 통과하는 단 하나의 `keys.canonical()` 로만 완전일치 매칭한다.

공개 API (채점 엔진):
  keys.SignalKey, keys.make_key, keys.canonical
  scorer.score, scorer.Scorecard
  metrics.recall_at_k, metrics.lift_at_k, metrics.review_reduction
  random_ranking.random_ranking  (스모크용 가짜 순위표)
"""

from scoring.keys import SignalKey, make_key, canonical
from scoring.scorer import score, Scorecard
from scoring import metrics, random_ranking

__all__ = [
    "SignalKey", "make_key", "canonical",
    "score", "Scorecard",
    "metrics", "random_ranking",
]

# 기본 K값 — 계획서 4-2 (검토대상 축소 관점)
DEFAULT_KS = (100, 500, 1000, 5000)
