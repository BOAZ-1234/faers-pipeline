"""채점기 전체 스모크 — 합성 정답 + 가짜 순위표로 배관 검증.

실행:  python -m scoring.smoke

FAERS·정답지 실데이터 없이도 채점기 전 구간(키→매칭→지표→성적표)이
돌아가는지 확인한다. 랜덤 순위표의 Lift 가 ~1.0, 신호 심은 순위표가 그보다
크면 지표 계산이 올바른 것.
"""

from __future__ import annotations

import sys

from scoring import DEFAULT_KS, make_key, score
from scoring.random_ranking import random_ranking


def _fake_labels(n_pos: int = 200, n_neg: int = 100):
    positives = {make_key(f"drug_{i}", f"reaction_{i}") for i in range(n_pos)}
    negatives = {make_key(f"drug_neg_{i}", f"reaction_neg_{i}") for i in range(n_neg)}
    return positives, negatives


def run() -> int:
    # Windows 콘솔(cp949)에서도 ≈ ✅ 등 출력되도록 UTF-8 강제
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    positives, negatives = _fake_labels()
    n_noise = 5000

    print("=" * 60)
    print("  [A] 완전 무작위 순위표  (기대: Lift ≈ 1.0)")
    print("=" * 60)
    rnd = random_ranking(positives, negatives, n_noise=n_noise,
                         signal_strength=0.0, seed=42)
    card_rnd = score(rnd, positives, ks=DEFAULT_KS)
    print(card_rnd.render())

    print()
    print("=" * 60)
    print("  [B] 양성을 앞으로 끌어올린 순위표  (기대: Lift ≫ 1.0)")
    print("=" * 60)
    planted = random_ranking(positives, negatives, n_noise=n_noise,
                             signal_strength=1.0, seed=42)
    card_planted = score(planted, positives, ks=DEFAULT_KS)
    print(card_planted.render())

    # 스모크 판정 — 배관이 맞으면 반드시 성립해야 하는 성질들
    r100_rnd = card_rnd.row(100)
    r100_planted = card_planted.row(100)
    checks = {
        "랜덤 Lift@100 이 1.0 근처(0.3~3.0)": 0.3 <= r100_rnd.lift <= 3.0,
        "신호 순위표가 랜덤보다 Recall@100 높음":
            r100_planted.recall > r100_rnd.recall,
        "신호 순위표 Lift@100 이 랜덤보다 큼":
            r100_planted.lift > r100_rnd.lift,
        "축소율@100 = 1 - 100/N 유효": 0 < r100_rnd.review_reduction < 1,
        "커버리지 100%(양성 전부 후보에 포함)":
            abs(card_rnd.coverage - 1.0) < 1e-9,
    }
    print()
    print("=" * 60)
    print("  스모크 체크")
    print("=" * 60)
    ok = True
    for name, passed in checks.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
        ok = ok and passed
    print()
    print("  결과:", "전체 통과 ✅" if ok else "실패 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
