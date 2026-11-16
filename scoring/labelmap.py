"""정답지(gold) → 순위표 키 매핑 (실사전 + vocab 인식 + 다중 PT 확장).

`mappers.py`는 1:1 얇은 매퍼지만, 실제 gold 는 조인키가 더 까다롭다:
  · product 가 "Ingredient (Brand)"/"Brand (Ingredient)" 형태 → 괄호 파싱 필요
  · product 가 순수 브랜드 → drug_ingredient_map(medicinalproduct→ingredient_norm) 필요
  · signal 이 "A, B, and C" 다중 반응 서술 → reaction_dict(signal→pt_set_id) + 쪼개기
  · 매핑 결과가 **순위표(parquet)의 vocab 에 실재해야** 조인됨

이 모듈은 순위표의 ingredient/pt vocab 을 받아, gold 를 **검증된 키로 확장**해
`build_labelset` 보다 커버리지를 끌어올린다. 사전이 없으면 파싱/쪼개기만으로 폴백.

커버리지(= 채점가능 양성/전체)가 Recall 상한이라, 결과에 funnel 통계도 같이 돌려준다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from scoring.keys import SignalKey, canonical, make_key
from scoring.ground_truth import (
    POSITIVE, NEGATIVE, PENDING, RESOLVED,
    terminal_pairs, choose_cutoff_year,
)

_PAREN = re.compile(r"[(]([^)]*)[)]")
_SPLIT = re.compile(r",|;|/|\band\b")


# ── 사전 로더 ────────────────────────────────────────────────────────────────
def load_drug_map(path: str | Path,
                  key_col: str = "medicinalproduct",
                  val_col: str = "ingredient_norm") -> dict[str, str]:
    """product(canonical) → ingredient_norm. drug_ingredient_map.csv."""
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str).fillna("")
    return {canonical(k): v for k, v in zip(df[key_col], df[val_col]) if v}


def load_reaction_map(path: str | Path,
                      key_col: str = "signal",
                      val_col: str = "pt_set_id") -> dict[str, list[str]]:
    """signal(canonical) → [pt...]. reaction_dict_final.csv (pt_set_id 는 |,; 로 멀티 가능)."""
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str).fillna("")
    out: dict[str, list[str]] = {}
    for s, p in zip(df[key_col], df[val_col]):
        out[canonical(s)] = [canonical(x) for x in re.split(r"[|,;]", p) if x.strip()]
    return out


# ── vocab 인식 리졸버 ────────────────────────────────────────────────────────
def _paren_variants(s: str) -> list[str]:
    s = str(s)
    before = re.split(r"[(]", s)[0]
    m = _PAREN.search(s)
    inside = m.group(1) if m else ""
    return [s, before, inside]


def make_ingredient_resolver(
    ing_vocab: set[str], drug_map: dict[str, str] | None = None
) -> Callable[[str], str | None]:
    """product → ingredient(canonical) or None. 순위표 vocab 에 있는 값만 반환."""
    drug_map = drug_map or {}

    def resolve(product: str) -> str | None:
        cands = _paren_variants(product)
        for c in cands:                       # 1) vocab 직매칭
            if canonical(c) in ing_vocab:
                return canonical(c)
        for c in cands:                       # 2) 사전 경유
            v = drug_map.get(canonical(c))
            if v and canonical(v) in ing_vocab:
                return canonical(v)
        return None

    return resolve


def make_reaction_resolver(
    pt_vocab: set[str], reaction_map: dict[str, list[str]] | None = None
) -> Callable[[str], set[str]]:
    """signal → {pt(canonical)...}. 사전 우선, 없으면 쪼개서 vocab 직매칭."""
    reaction_map = reaction_map or {}

    def resolve(signal: str) -> set[str]:
        key = canonical(signal)
        if key in reaction_map:
            hit = {x for x in reaction_map[key] if x in pt_vocab}
            if hit:
                return hit
        out: set[str] = set()
        if key in pt_vocab:
            out.add(key)
        for part in _SPLIT.split(str(signal)):
            cp = canonical(part)
            if cp in pt_vocab:
                out.add(cp)
        return out

    return resolve


# ── 확장 라벨셋 ──────────────────────────────────────────────────────────────
@dataclass
class EvalLabelSet:
    positives: set[SignalKey]
    negatives: set[SignalKey]
    sealed: set[SignalKey]
    cutoff_year: int
    coverage: dict = field(default_factory=dict)   # funnel 통계

    def summary(self) -> str:
        c = self.coverage
        return (
            f"컷오프 {self.cutoff_year}  |  양성 {len(self.positives):,} / "
            f"음성 {len(self.negatives):,} / 봉인 {len(self.sealed):,}  |  "
            f"양성 pair 커버리지(키생성) {c.get('pos_keyed', 0)}/{c.get('pos_pairs', 0)} "
            f"({c.get('pos_coverage', 0):.1%})"
        )


def build_eval_labelset(
    gold: pd.DataFrame,
    *,
    ing_vocab: set[str],
    pt_vocab: set[str],
    drug_map: dict[str, str] | None = None,
    reaction_map: dict[str, list[str]] | None = None,
    min_resolution_rate: float = 0.75,
) -> EvalLabelSet:
    """gold → vocab 검증·다중PT 확장 라벨셋. ing_vocab/pt_vocab 은 순위표에서 뽑는다."""
    ing_of = make_ingredient_resolver(ing_vocab, drug_map)
    rx_of = make_reaction_resolver(pt_vocab, reaction_map)

    pairs = terminal_pairs(gold)
    cutoff = choose_cutoff_year(pairs, min_resolution_rate)

    def keys_for(product: str, signal: str) -> set[SignalKey]:
        ing = ing_of(product)
        if not ing:
            return set()
        return {make_key(ing, r) for r in rx_of(signal)}

    scored = pairs[(pairs["first_year"] <= cutoff) & (pairs["label"].isin(RESOLVED))]
    pos_rows = scored[scored["label"] == POSITIVE]
    neg_rows = scored[scored["label"] == NEGATIVE]
    sealed_rows = pairs[pairs["label"] == PENDING]

    positives: set[SignalKey] = set()
    pos_keyed = 0
    for p, s in zip(pos_rows["product"], pos_rows["signal"]):
        ks = keys_for(p, s)
        if ks:
            pos_keyed += 1
            positives |= ks
    negatives: set[SignalKey] = set()
    for p, s in zip(neg_rows["product"], neg_rows["signal"]):
        negatives |= keys_for(p, s)
    sealed: set[SignalKey] = set()
    for p, s in zip(sealed_rows["product"], sealed_rows["signal"]):
        sealed |= keys_for(p, s)

    positives = {k for k in positives if k.is_valid()}
    negatives = {k for k in negatives if k.is_valid()}
    sealed = {k for k in sealed if k.is_valid()}

    return EvalLabelSet(
        positives=positives, negatives=negatives, sealed=sealed,
        cutoff_year=cutoff,
        coverage={
            "pos_pairs": len(pos_rows),
            "pos_keyed": pos_keyed,
            "pos_coverage": pos_keyed / len(pos_rows) if len(pos_rows) else 0.0,
            "pos_keys": len(positives),
        },
    )


def vocab_from_signals(signals: pd.DataFrame,
                       ingredient_col: str = "ingredient",
                       pt_col: str = "pt") -> tuple[set[str], set[str]]:
    """순위표 DF → (ingredient vocab, pt vocab) — 둘 다 canonical."""
    ing = {canonical(x) for x in signals[ingredient_col].dropna().unique()}
    pt = {canonical(x) for x in signals[pt_col].dropna().unique()}
    return ing, pt
