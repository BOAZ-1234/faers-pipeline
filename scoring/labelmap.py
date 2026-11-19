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

# ── 폴백 정규화(기존 매칭 실패 시에만 추가 후보 생성 → 순증 보장) ──────────────
# 성분: 염·제형·용량 노이즈 제거 + 복합제/`and` 분해
_SALT = re.compile(
    r"\b(hydrochloride|hcl|mesylate|meglumine|sodium|potassium|calcium|sulfate|sulphate|"
    r"phosphate|acetate|citrate|tartrate|maleate|succinate|fumarate|besylate|bromide|"
    r"chloride|nitrate|dihydrate|monohydrate|hydrobromide|tosylate|disodium|hemifumarate|"
    r"valerate|propionate|xinafoate|dipropionate|furoate|pamoate|base)\b")
_FORM = re.compile(
    r"\b(extended release|delayed release|modified release|oral|injection|tablet|tablets|"
    r"capsule|capsules|solution|suspension|cream|gel|ointment|topical|intravenous|"
    r"inhalation|nasal|ophthalmic|patch|spray)\b")
_DOSE = re.compile(r"\d+\.?\d*\s?(mg|mcg|ml|units?|iu|%)\b|\d+\.?\d*\s?g\b|\d+\.?\d*%")
# 반응: 서술 접두어 제거
_PHRASE = re.compile(
    r"^(interaction resulting in|overdose due to|adverse events? from|risk of|cases of|"
    r"reports of|prolongation of the|labeling confusion resulting in|dosing confusion)\s*",
    re.I)
# 반응: 미국식→영국식 철자 (MedDRA PT 는 영국식)
_BRITISH = [
    ("hemorrhage", "haemorrhage"), ("hemo", "haemo"), ("hema", "haema"),
    ("tumor", "tumour"), ("edema", "oedema"), ("diarrhea", "diarrhoea"),
    ("anemia", "anaemia"), ("esophag", "oesophag"), ("gynec", "gynaec"),
    ("leukemia", "leukaemia"), ("leukopenia", "leucopenia"), ("bleeding", "haemorrhage"),
    ("fetal", "foetal"), ("estrogen", "oestrogen"), ("fecal", "faecal"),
    ("anesth", "anaesth"), ("paresthesia", "paraesthesia"), ("celiac", "coeliac"),
]


def _to_british(s: str) -> str:
    for a, b in _BRITISH:
        s = s.replace(a, b)
    # 접미사 패턴 (이미 변환된 형태엔 재적용 안 되게 가드) — 폴백 후보 전용이라 안전
    s = re.sub(r"(?<![a])emia\b", "aemia", s)           # -emia → -aemia (hypocalcemia…)
    s = re.sub(r"iz(e|es|ed|ing|ation)\b", r"is\1", s)  # -ize → -ise (necrotizing…)
    s = re.sub(r"yz(e|es|ed|ing)\b", r"ys\1", s)        # -yze → -yse (paralyze…)
    return s


# ── 사전 로더 ────────────────────────────────────────────────────────────────
def load_drug_map(path: str | Path,
                  key_col: str = "medicinalproduct",
                  val_col: str = "ingredient_norm") -> dict[str, str]:
    """product(canonical) → ingredient_norm. drug_ingredient_map.csv."""
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str).fillna("")
    # canonical(k) 가 빈 문자열인 행 제외 — medicinalproduct 빈칸 쓰레기행이 빈 키로
    # 들어가면 매칭 실패한 product 가 전부 그 값으로 오매칭됨(커버리지 뻥튀기 버그).
    return {canonical(k): v for k, v in zip(df[key_col], df[val_col]) if v and canonical(k)}


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
# 정답지 product 가 '약물 클래스/군' 수준인지 (성분단위 스코어링 범위 밖) — 휴리스틱.
# FDA 안전성 공지가 성분이 아니라 클래스로 내려온 행(NSAIDs, TNF blockers 등)을 걸러
# 커버리지 분모에서 제외한다. 완전정확한 분류가 아니라 범위 정의용 1차 필터.
_CLASS = re.compile(
    r"inhibitor|blocker|agonist|antagonist|\bagents?\b|\bdrugs?\b|\bproducts?\b|"
    r"nsaid|ssri|snri|modulator|corticosteroid|contrast media|\bstatins?\b|"
    r"antivirals?|\bopioid", re.I)


def is_class_level(product: str) -> bool:
    """product 가 성분이 아니라 약물 클래스/군 수준이면 True (스코어링 범위 밖)."""
    return bool(_CLASS.search(str(product)))


def _paren_variants(s: str) -> list[str]:
    s = str(s)
    before = re.split(r"[(]", s)[0]
    m = _PAREN.search(s)
    inside = m.group(1) if m else ""
    return [s, before, inside]


def _ing_fallback_variants(product: str) -> list[str]:
    """염·제형·용량 제거 + 괄호 제거 + 복합제/`and` 분해 후보 (폴백 전용)."""
    noparen = str(product).replace("(", " ").replace(")", " ")
    before = re.split(r"[(]", str(product))[0]
    m = _PAREN.search(str(product))
    inside = m.group(1) if m else ""
    out: list[str] = []
    for b in (x for x in (noparen, before, inside) if x.strip()):
        strip = _FORM.sub(" ", _SALT.sub(" ", _DOSE.sub(" ", b)))
        out.append(strip)
        for part in re.split(r"[/,]|\band\b", b):      # 복합제·'and' 분해
            if part.strip():
                out.append(part)
                out.append(_FORM.sub(" ", _SALT.sub(" ", _DOSE.sub(" ", part))))
    return out


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
        for c in _ing_fallback_variants(product):   # 3) 폴백(염/제형/복합제 등)
            cc = canonical(c)
            if cc in ing_vocab:
                return cc
            v = drug_map.get(cc)
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
        if out:
            return out
        # 폴백(위 직매칭 실패 시만): 영국식 철자·하이픈·복수·서술어·괄호약어 제거
        s = str(signal)
        nop = re.split(r"[(]", s)[0]           # 괄호약어 앞 본문 (SLE/DRESS/TEN/AGEP…)
        forms = {s, _to_british(s), s.replace("-", " "), _to_british(s).replace("-", " "),
                 _PHRASE.sub("", s), _to_british(_PHRASE.sub("", s)),
                 nop, _to_british(nop), nop.replace("-", " "),
                 _to_british(nop).replace("-", " ")}
        cands: set[str] = set()
        for f in forms:
            cf = canonical(f)
            cands.add(cf)
            cands.add(re.sub(r"s\b", "", cf))  # 복수 제거
            for part in _SPLIT.split(f):
                cands.add(canonical(part))
                cands.add(canonical(_to_british(part)))
        return {c for c in cands if c and c in pt_vocab}

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
        cls = c.get("pos_class_excluded", 0)
        cls_s = f"  (클래스 {cls} 제외)" if cls else ""
        return (
            f"컷오프 {self.cutoff_year}  |  양성 {len(self.positives):,} / "
            f"음성 {len(self.negatives):,} / 봉인 {len(self.sealed):,}  |  "
            f"양성 pair 커버리지(키생성) {c.get('pos_keyed', 0)}/{c.get('pos_pairs', 0)} "
            f"({c.get('pos_coverage', 0):.1%}){cls_s}"
        )


def build_eval_labelset(
    gold: pd.DataFrame,
    *,
    ing_vocab: set[str],
    pt_vocab: set[str],
    drug_map: dict[str, str] | None = None,
    reaction_map: dict[str, list[str]] | None = None,
    min_resolution_rate: float = 0.75,
    exclude_class_level: bool = True,
) -> EvalLabelSet:
    """gold → vocab 검증·다중PT 확장 라벨셋. ing_vocab/pt_vocab 은 순위표에서 뽑는다.

    exclude_class_level=True (기본): 정답지 product 가 약물 클래스/군 수준인 행은
    성분단위 스코어링 범위 밖이라 **양성 분모에서 제외**(커버리지 정의). [[is_class_level]]
    """
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

    # 클래스 수준 양성은 성분단위 범위 밖 → 분모에서 제외(기본)
    n_class = int(pos_rows["product"].map(is_class_level).sum())
    if exclude_class_level:
        pos_rows = pos_rows[~pos_rows["product"].map(is_class_level)]

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
            "pos_class_excluded": n_class if exclude_class_level else 0,
        },
    )


def vocab_from_signals(signals: pd.DataFrame,
                       ingredient_col: str = "ingredient",
                       pt_col: str = "pt") -> tuple[set[str], set[str]]:
    """순위표 DF → (ingredient vocab, pt vocab) — 둘 다 canonical."""
    ing = {canonical(x) for x in signals[ingredient_col].dropna().unique()}
    pt = {canonical(x) for x in signals[pt_col].dropna().unique()}
    return ing, pt
