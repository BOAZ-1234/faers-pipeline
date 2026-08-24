"""채점기 본체 — 순위표 + 정답지 → 성적표(Scorecard).

완전일치 매칭: 순위표의 각 SignalKey 가 정답 양성 집합에 있으면 hit.
정규화는 keys.canonical() 하나로 통일돼 있으므로 여기선 집합 조회(O(1))만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from scoring import metrics
from scoring.keys import SignalKey

DEFAULT_KS = (100, 500, 1000, 5000)


@dataclass(frozen=True)
class KRow:
    """K 하나에 대한 지표 한 줄."""

    k: int
    hits: int
    recall: float
    precision: float
    lift: float
    review_reduction: float


@dataclass
class Scorecard:
    n_ranked: int             # 순위표 고유 후보 수
    n_universe: int              # 랜덤 기준선 분모 N (전체 후보 조합)
    n_positives_total: int       # 정답 양성 총수 (라벨셋 전체)
    n_positives_scorable: int    # 그중 후보 우주에 실제로 존재하는 수 = Recall 분모
    rows: list[KRow] = field(default_factory=list)

    def row(self, k: int) -> KRow | None:
        for r in self.rows:
            if r.k == k:
                return r
        return None

    @property
    def coverage(self) -> float:
        """양성 중 몇 %가 애초에 채점 가능한가 (이름매칭 한계 → Recall 상한).

        계획서 L123~125의 '커버리지' — 순위표에 존재할 수조차 없는 쌍은 채점 불가.
        """
        return metrics.recall_at_k(self.n_positives_scorable, self.n_positives_total)

    def to_dict(self) -> dict:
        return {
            "n_ranked": self.n_ranked,
            "n_universe": self.n_universe,
            "n_positives_total": self.n_positives_total,
            "n_positives_scorable": self.n_positives_scorable,
            "coverage": round(self.coverage, 4),
            "by_k": {
                r.k: {
                    "hits": r.hits,
                    "recall": round(r.recall, 4),
                    "precision": round(r.precision, 4),
                    "lift": round(r.lift, 2),
                    "review_reduction": round(r.review_reduction, 4),
                }
                for r in self.rows
            },
        }

    def render(self) -> str:
        lines = [
            f"순위표 후보 {self.n_ranked:,}  |  전체 N {self.n_universe:,}  |  "
            f"양성 총 {self.n_positives_total:,} → 채점가능 {self.n_positives_scorable:,} "
            f"(커버리지 {self.coverage:.1%})",
            f"{'K':>7} {'hits':>6} {'Recall':>8} {'Prec':>7} {'Lift':>7} {'축소율':>8}",
            "-" * 46,
        ]
        for r in self.rows:
            lines.append(
                f"{r.k:>7,} {r.hits:>6} {r.recall:>7.1%} {r.precision:>6.1%} "
                f"{r.lift:>6.1f}x {r.review_reduction:>7.1%}"
            )
        return "\n".join(lines)


def _dedup_keep_order(ranking: Iterable[SignalKey]) -> list[SignalKey]:
    seen: set[SignalKey] = set()
    out: list[SignalKey] = []
    for key in ranking:
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def score(
    ranking: Sequence[SignalKey],
    positives: Iterable[SignalKey],
    *,
    universe_size: int | None = None,
    ks: Sequence[int] = DEFAULT_KS,
) -> Scorecard:
    """순위표를 정답지로 채점한다.

    ranking       : 상위(신호 강함)부터 정렬된 SignalKey 목록 = 후보 우주.
    positives     : 정답 양성 SignalKey 집합 (라벨셋 전체).
    universe_size : 랜덤 기준선 분모 N. 없으면 고유 후보 수로 본다.
    ks            : 평가할 K값들.

    Recall/Lift 의 분모는 '전체 양성'이 아니라 **채점 가능한 양성**
    (=후보 우주에 실제로 존재하는 양성)이다. 순위표에 있을 수조차 없는 쌍을
    오답으로 세지 않기 위함 — 계획서 3장 ②, Precision 미채택 사유(L150~153).
    """
    ranked = _dedup_keep_order(ranking)
    pos_set = set(positives)

    n_ranked = len(ranked)
    n_universe = universe_size if universe_size is not None else n_ranked
    n_universe = max(n_universe, n_ranked)  # N 은 최소한 후보 수 이상
    n_pos_total = len(pos_set)

    ranked_labels = [key in pos_set for key in ranked]
    n_pos_scorable = sum(ranked_labels)  # 후보 우주에 존재하는 양성 = Recall 분모

    rows: list[KRow] = []
    for k in sorted(ks):
        hits = metrics.hits_at(ranked_labels, k)
        rows.append(
            KRow(
                k=k,
                hits=hits,
                recall=metrics.recall_at_k(hits, n_pos_scorable),
                precision=metrics.precision_at_k(hits, min(k, n_ranked) or k),
                lift=metrics.lift_at_k(hits, min(k, n_ranked) or k, n_pos_scorable, n_universe),
                review_reduction=metrics.review_reduction_at_k(k, n_universe),
            )
        )

    return Scorecard(
        n_ranked=n_ranked,
        n_universe=n_universe,
        n_positives_total=n_pos_total,
        n_positives_scorable=n_pos_scorable,
        rows=rows,
    )
