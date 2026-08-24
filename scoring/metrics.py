"""채점 지표 — 순수 함수. 데이터 의존 없음.

입력 공통:
  hits_at_k : 상위 K개 안에 든 정답(양성) 개수 (누적)
  n_pos     : 정답(양성) 총 개수  = Recall 분모
  n_universe: 후보 전체 개수 N     = 랜덤 기준선의 분모

Recall@K   = hits@K / n_pos
Precision@K= hits@K / K
Lift@K     = Precision@K / (n_pos / n_universe)   … 랜덤 대비 몇 배
축소율@K   = 1 - K / n_universe                    … 전체 대신 K개만 검토
"""

from __future__ import annotations


def recall_at_k(hits_at_k: int, n_pos: int) -> float:
    """상위 K에서 잡은 양성 비율. n_pos=0 이면 정의 안 됨 → 0.0."""
    if n_pos <= 0:
        return 0.0
    return hits_at_k / n_pos


def precision_at_k(hits_at_k: int, k: int) -> float:
    """상위 K 중 양성 비율. k=0 이면 0.0."""
    if k <= 0:
        return 0.0
    return hits_at_k / k


def lift_at_k(hits_at_k: int, k: int, n_pos: int, n_universe: int) -> float:
    """랜덤 대비 배수 = Precision@K / 기저율(n_pos/n_universe).

    Lift=1 이면 랜덤과 동일, >1 이면 순위표가 양성을 앞으로 끌어올린 것.
    기저율 0(양성 없음)이면 정의 안 됨 → 0.0.
    """
    if k <= 0 or n_universe <= 0 or n_pos <= 0:
        return 0.0
    base_rate = n_pos / n_universe
    return precision_at_k(hits_at_k, k) / base_rate


def review_reduction_at_k(k: int, n_universe: int) -> float:
    """상위 K개만 검토할 때의 검토대상 축소율 = 1 - K/N.

    N개 전부 대신 K개만 보면 되므로 (1 - K/N) 만큼 검토량이 줄어든다.
    K>=N 이면 0.0 (줄인 게 없음).
    """
    if n_universe <= 0:
        return 0.0
    return max(0.0, 1.0 - k / n_universe)


def k_for_recall(ranked_labels: list[bool], target_recall: float) -> int | None:
    """목표 Recall 을 처음 달성하는 최소 K.

    ranked_labels: 순위 순서대로의 양성여부(True/False) 리스트.
    없으면(끝까지 목표 미달) None.
    """
    n_pos = sum(ranked_labels)
    if n_pos <= 0:
        return None            # 양성 없음 → 정의 안 됨
    if target_recall <= 0:
        return 0               # 목표 0 → 자명하게 달성
    need = target_recall * n_pos
    hits = 0
    for i, is_pos in enumerate(ranked_labels, start=1):
        if is_pos:
            hits += 1
        if hits >= need:
            return i
    return None


def cumulative_hits(ranked_labels: list[bool]) -> list[int]:
    """순위별 누적 양성 개수 [h@1, h@2, ...]. 곡선/여러 K 재사용용."""
    out, h = [], 0
    for is_pos in ranked_labels:
        h += 1 if is_pos else 0
        out.append(h)
    return out


def hits_at(ranked_labels: list[bool], k: int) -> int:
    """상위 K개 안의 양성 개수 (K가 리스트보다 크면 전체 양성)."""
    return sum(ranked_labels[:k])
