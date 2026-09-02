"""정답지 라벨셋 구성 — 우측절단 대응 코호트 분할.

원본: FDA 분기 신호보고서를 3분류(양성/음성/보류)한 표
      (컬럼: product, signal, year, q_start, label)
흐름:
  1) (product, signal) 쌍으로 묶어 **종결 라벨** 결정 (양성 > 음성 > 보류)
  2) 최초등장 코호트(연/분기)별 판정완료율을 보고 **컷오프 연도** 결정
  3) 채점셋(판정완료 & 컷오프 이하) / 봉인예측셋(보류) 으로 분할

정규화(product→성분, signal→부작용 PT)는 이 모듈이 하지 않는다.
build_labelset() 에 mapper 를 주입해 SignalKey 로 바꾼다(기본은 원문 그대로).
_probe/define_eval_window.py 프로토타입을 scoring 패키지로 재구축한 것.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from scoring.keys import SignalKey, make_key

# 분기 시작월 → 분기 인덱스
QMAP = {"January": 1, "April": 2, "July": 3, "October": 4}
# 종결 라벨 우선순위 (결정된 상태 우선)
LABEL_RANK = {"양성": 2, "음성": 1, "보류": 0}
POSITIVE, NEGATIVE, PENDING = "양성", "음성", "보류"

# 채점셋에 포함할 판정완료 라벨
RESOLVED = (POSITIVE, NEGATIVE)


def _cohort(year: int, q_start: str) -> int:
    """정렬용 코호트 정수. 2022 Q1 → 20221."""
    qidx = QMAP.get(str(q_start), 1)
    return int(year) * 10 + qidx


def terminal_pairs(df: pd.DataFrame) -> pd.DataFrame:
    """원본 행 → 고유 (product, signal) 쌍 + 종결 라벨/최초등장.

    반환 컬럼: product, signal, label, first_cohort, first_year, n_quarters
    """
    d = df.copy()
    d["_cohort"] = [_cohort(y, q) for y, q in zip(d["year"], d["q_start"])]
    d["_rank"] = d["label"].map(LABEL_RANK).fillna(0)

    def agg(g: pd.DataFrame) -> pd.Series:
        best = g.loc[g["_rank"].idxmax()]
        return pd.Series(
            {
                "label": best["label"],
                "first_cohort": int(g["_cohort"].min()),
                "first_year": int(pd.to_numeric(g["year"]).min()),
                "n_quarters": g["quarter_label"].nunique()
                if "quarter_label" in g
                else g["_cohort"].nunique(),
            }
        )

    pairs = (
        d.groupby(["product", "signal"], sort=False)
        .apply(agg, include_groups=False)
        .reset_index()
    )
    return pairs


def resolution_table(pairs: pd.DataFrame) -> pd.DataFrame:
    """최초등장 연도별 판정완료율 표 (컷오프 근거)."""
    tab = pairs.pivot_table(
        index="first_year", columns="label", values="product",
        aggfunc="count", fill_value=0,
    )
    for c in (POSITIVE, NEGATIVE, PENDING):
        if c not in tab:
            tab[c] = 0
    tab["계"] = tab[POSITIVE] + tab[NEGATIVE] + tab[PENDING]
    tab["판정완료"] = tab[POSITIVE] + tab[NEGATIVE]
    tab["완료율"] = (tab["판정완료"] / tab["계"]).where(tab["계"] > 0, 0.0)
    return tab


def choose_cutoff_year(pairs: pd.DataFrame, min_resolution_rate: float = 0.75) -> int:
    """판정완료율이 기준 이상인 가장 최근 연도. (절벽 직전까지 포함)"""
    tab = resolution_table(pairs)
    ok = tab[tab["완료율"] >= min_resolution_rate]
    if len(ok):
        return int(ok.index.max())
    return int(tab.index.min())


@dataclass
class LabelSet:
    """채점에 바로 쓰는 정답 집합."""

    positives: set[SignalKey]     # 채점셋 양성
    negatives: set[SignalKey]     # 채점셋 음성 (기저율/오탐 확인용)
    sealed: set[SignalKey]        # 봉인예측셋 (보류=미판정, 미래검증용)
    cutoff_year: int
    pairs: pd.DataFrame           # 종결 라벨 원표 (감사/디버깅용)

    def summary(self) -> str:
        return (
            f"컷오프 {self.cutoff_year}  |  "
            f"채점셋 양성 {len(self.positives):,} / 음성 {len(self.negatives):,}  |  "
            f"봉인예측 {len(self.sealed):,}"
        )


_identity = lambda s: s  # noqa: E731  기본 매퍼: 원문 그대로


def build_labelset(
    df: pd.DataFrame,
    *,
    ingredient_of: Callable[[str], str] = _identity,
    reaction_of: Callable[[str], str] = _identity,
    min_resolution_rate: float = 0.75,
) -> LabelSet:
    """분류된 정답 표 → LabelSet.

    ingredient_of / reaction_of: (product/signal 원문) → 정규화 문자열.
      기본은 원문 그대로(항등). 이수연 drug_dict / 하경 reaction_dict 가
      준비되면 이 두 함수만 주입하면 키가 맞춰진다(계획서 노트 ③).
    """
    pairs = terminal_pairs(df)
    cutoff = choose_cutoff_year(pairs, min_resolution_rate)

    def key_of(product: str, signal: str) -> SignalKey:
        return make_key(ingredient_of(product), reaction_of(signal))

    scored = pairs[
        (pairs["first_year"] <= cutoff) & (pairs["label"].isin(RESOLVED))
    ]
    positives = {
        key_of(p, s)
        for p, s in zip(
            scored.loc[scored["label"] == POSITIVE, "product"],
            scored.loc[scored["label"] == POSITIVE, "signal"],
        )
    }
    negatives = {
        key_of(p, s)
        for p, s in zip(
            scored.loc[scored["label"] == NEGATIVE, "product"],
            scored.loc[scored["label"] == NEGATIVE, "signal"],
        )
    }
    sealed_df = pairs[pairs["label"] == PENDING]
    sealed = {key_of(p, s) for p, s in zip(sealed_df["product"], sealed_df["signal"])}

    # 유효하지 않은 키(빈 성분/부작용) 제거
    positives = {k for k in positives if k.is_valid()}
    negatives = {k for k in negatives if k.is_valid()}
    sealed = {k for k in sealed if k.is_valid()}

    return LabelSet(
        positives=positives,
        negatives=negatives,
        sealed=sealed,
        cutoff_year=cutoff,
        pairs=pairs,
    )
