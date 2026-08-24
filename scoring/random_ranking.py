"""가짜 순위표 생성기 — 채점기 스모크 테스트용.

계획서: "순서를 무작위로 섞은 가짜 순위표로 전체 스모크 테스트."
FAERS 순위표가 아직 없어도 채점기 전체 배관을 검증할 수 있게 한다.

signal_strength 로 두 극단을 만든다:
  0.0 → 완전 무작위. 기대 Recall@K ≈ K/N, Lift ≈ 1.0  (랜덤 기준선 검증)
  1.0 → 양성을 맨 앞에 배치. Lift ≫ 1.0                (지표가 신호를 잡는지)
"""

from __future__ import annotations

import random
from typing import Iterable

from scoring.keys import SignalKey, make_key


def _noise_pairs(n: int, rng: random.Random, avoid: set[SignalKey]) -> list[SignalKey]:
    """정답과 겹치지 않는 합성 후보 n개."""
    out: list[SignalKey] = []
    i = 0
    while len(out) < n:
        key = make_key(f"noise_drug_{i}", f"noise_reaction_{i % 500}")
        i += 1
        if key not in avoid:
            out.append(key)
    return out


def random_ranking(
    positives: Iterable[SignalKey],
    negatives: Iterable[SignalKey] = (),
    *,
    n_noise: int = 5000,
    signal_strength: float = 0.0,
    seed: int = 0,
) -> list[SignalKey]:
    """후보 전체(양성+음성+잡음)를 만들어 순위표 순서로 반환.

    signal_strength ∈ [0,1]: 양성을 앞으로 끌어올리는 정도.
    반환 리스트 = 순위표(상위부터). 전체 길이가 곧 후보 우주 N.
    """
    rng = random.Random(seed)
    pos = list(dict.fromkeys(positives))
    neg = list(dict.fromkeys(negatives))
    avoid = set(pos) | set(neg)
    noise = _noise_pairs(n_noise, rng, avoid)

    universe = pos + neg + noise
    rng.shuffle(universe)

    if signal_strength <= 0:
        return universe

    # 양성 중 strength 비율을 뽑아 맨 앞으로 (나머지는 섞인 자리에 유지)
    pos_set = set(pos)
    promote_n = int(round(len(pos) * min(1.0, signal_strength)))
    promote = set(rng.sample(pos, promote_n)) if promote_n else set()
    front = [k for k in universe if k in promote]
    back = [k for k in universe if k not in promote]
    return front + back
