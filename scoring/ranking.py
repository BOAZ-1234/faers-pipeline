"""순위표 생성 — 버전 사다리(v1~v5).

계획서 §4-7 "한꺼번에 만들지 않는다" 표를 코드로 옮긴 것. 채점기(scoring/)는
**순위표를 받아 채점만** 한다. 이 모듈은 그 순위표를 `fct_signal_scores`(GOLD,
성분×반응 IC025)에서 만들어낸다.

버전 사다리 (project_plan §4-7):
  v1  IC025 단독                      파이프라인 완주 + 채점 작동 확인
  v2  + 시점 라벨 필터                표준 절차이자 신규성의 핵심(이진 제거)
  v3  + 중대성(사망분율)              표준 필수 항목
  v4  + 끊음/재투여(dechal/rechal)    계열이 다른 신규 재료
  v5  + 최근성 (층화…)               여유가 되는 만큼

설계 원칙 (계획서 §4-7 "가중치는 손으로 정하지 않는다"):
  · 게이트 임계값(IC025>0, a≥3)  → **고정** (WHO-UMC/MHRA 표준값)
  · 라벨 필터                    → **고정** 이진 제거
  · 랭킹 가중치                  → **학습** (shrinkage logistic regression)
그래서 v1·v2는 IC025로 그냥 정렬하고(학습 불필요), v3부터 피처가 2개 이상이 되어
로지스틱 회귀로 결합한다. 정규화(shrinkage)가 작은 학습셋의 과적합을 억제한다.

v1e(증거량 가중, 헤드라인 재현용):
  v1(IC025 단독)과 v3(학습 결합) 사이에, 새 피처·학습 없이 **기존 증거량만 반영**한
  `IC025 × log(a)` 변형. 흔한 신호(a 큼)의 순위를 끌어올려 IC025 단독의 구조적 한계를
  보완한다. 학습이 없어 누수가 원천적으로 없다(정답지와 무관하게 점수가 정해진다).

누수 방지 (학습 결합 v3~v5):
  로지스틱 회귀는 정답지(positives/negatives)로 학습하므로, 학습에 쓴 양성을 그대로
  채점하면 성능이 과대평가된다(in-sample). 그래서 **후보 전체를 cross-fit 으로 채점**한다
  — 후보를 k 겹으로 나눠, 각 겹은 그 겹을 뺀 라벨로 학습한 모델 하나가 라벨 유무와
  상관없이 전부 채점하고, 겹 안 백분위로 바꿔 합친다. 라벨 점과 라벨 없는 점을 서로
  다른 모델로 채점하면 겹별 절편 차이가 순위로 새므로(2026-11-19 실데이터 확인) 피한다.

데이터 가용성(2026-11 기준):
  · v1/v1e/v2 — `fct_signal_scores`의 `ic025`·`a`만 있으면 됨 → **지금 실행 가능**.
  · v3~v5 — 중대성/끊음·재투여/최근성 피처는 아직 GOLD 테이블에 없다. signals DF에
    해당 컬럼이 없으면 `FeatureUnavailable`로 "무슨 컬럼이 필요한지" 알려준다
    (가짜로 채우지 않음). 피처 테이블이 생기면 그 컬럼만 조인해 넘기면 바로 돈다.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from scoring.keys import SignalKey, make_key

# ── 기본 입력 컬럼 (fct_signal_scores) ──────────────────────────────────────
INGREDIENT_COL = "ingredient"
REACTION_COL = "pt"
COUNT_COL = "a"          # 동시출현 case 수 (게이트 a≥3 용)
IC025_COL = "ic025"

# 피처 이름 → signals DF에서 찾을 컬럼명. v3+ 피처는 상류 집계가 채워줘야 한다.
FEATURE_COLUMNS: dict[str, str] = {
    "ic025": "ic025",         # BCPNN 신용구간 하한 (v1)
    "death_frac": "death_frac",     # 이 신호 case 중 seriousnessdeath=1 비율 (v3)
    "dechal_frac": "dechal_frac",   # actiondrug 로 '중단 후 호전' 비율 (v4)
    "rechal_frac": "rechal_frac",   # drugrecurreadministration '재투여 후 재발' 비율 (v4)
    "recency": "recency",     # 최근성 점수(예: 최근 N년 신고 비중) (v5)
}

# v3+ 피처가 어느 상류 집계에서 와야 하는지 — FeatureUnavailable 메시지에 쓴다.
FEATURE_SOURCE_HINT: dict[str, str] = {
    "death_frac": "faers_demo.seriousnessdeath (신고 100% 채움) 를 신호별 평균",
    "dechal_frac": "faers_drug.actiondrug (76.7% 채움) 값 있는 case 중 '중단·호전' 비율",
    "rechal_frac": "faers_drug.drugrecurreadministration (19.8% 채움) '재투여 후 재발' 비율",
    "recency": "faers_demo.receivedate 로 최근성(예: 최근 3년 신고 비중)",
}


class FeatureUnavailable(RuntimeError):
    """요청한 버전이 필요로 하는 피처 컬럼이 signals DF에 없을 때."""


@dataclass(frozen=True)
class Gate:
    """고정 게이트(WHO-UMC/MHRA 표준). 후보 전수에서 노이즈를 떨어뜨린다."""

    a_min: int = 1          # 동시출현 최소 건수 (표준 a≥3)
    ic025_min: float | None = None  # IC025 하한 (표준 >0). None=미적용

    @classmethod
    def standard(cls) -> "Gate":
        """계획서 표준 게이트: a≥3 AND IC025>0."""
        return cls(a_min=3, ic025_min=0.0)


@dataclass(frozen=True)
class VersionSpec:
    """버전 하나의 조립 규칙."""

    name: str
    features: tuple[str, ...]    # 랭킹에 쓰는 피처 (순서 = 로지스틱 입력 순서)
    use_label_filter: bool       # 시점 라벨 이진 제거 적용 여부 (v2+)
    combiner: str                # "ic025"(정렬) | "logreg"(학습 결합)
    note: str = ""


VERSIONS: dict[str, VersionSpec] = {
    "v1": VersionSpec("v1", ("ic025",), False, "ic025", "IC025 단독 — 파이프라인 완주"),
    "v1e": VersionSpec("v1e", ("ic025",), False, "ic025_log_a",
                       "IC025 × log(a) — 증거량 가중(헤드라인 재현용, 학습 없음)"),
    "v2": VersionSpec("v2", ("ic025",), True, "ic025", "+ 시점 라벨 필터(이진 제거)"),
    "v3": VersionSpec("v3", ("ic025", "death_frac"), True, "logreg", "+ 중대성(사망분율)"),
    "v4": VersionSpec(
        "v4", ("ic025", "death_frac", "dechal_frac", "rechal_frac"), True, "logreg",
        "+ 끊음/재투여",
    ),
    "v5": VersionSpec(
        "v5",
        ("ic025", "death_frac", "dechal_frac", "rechal_frac", "recency"),
        True, "logreg", "+ 최근성",
    ),
}


# ── shrinkage(L2) 로지스틱 회귀 — numpy 최소 구현 ───────────────────────────
# sklearn 의존 없이도 재현 가능하게. 피처 표준화 후 L2 정규화 경사하강.
# 계획서: "학습셋이 1,142건이라 정규화가 과적합을 억제하고, 계수를 그대로 읽어
# '어느 재료가 얼마나 기여했나'를 보고할 수 있다."

def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


@dataclass
class LogRegModel:
    w: np.ndarray
    b: float
    mu: np.ndarray
    sd: np.ndarray
    feature_names: tuple[str, ...]

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        Xs = (X - self.mu) / self.sd
        return _sigmoid(Xs @ self.w + self.b)

    def coef_report(self) -> dict[str, float]:
        """표준화 공간 계수 — '어느 재료가 얼마나 기여했나' 보고용."""
        return {n: float(c) for n, c in zip(self.feature_names, self.w)}


def fit_shrinkage_logreg(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: Sequence[str],
    *,
    l2: float = 1.0,
    iters: int = 3000,
    lr: float = 0.5,
) -> LogRegModel:
    """L2 정규화 로지스틱 회귀. 결정적(난수 없음)."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    Xs = (X - mu) / sd
    n, d = Xs.shape
    w = np.zeros(d)
    b = 0.0
    for _ in range(iters):
        p = _sigmoid(Xs @ w + b)
        g = p - y
        gw = Xs.T @ g / n + l2 * w / n   # 절편 b 에는 벌점 없음
        gb = g.mean()
        w -= lr * gw
        b -= lr * gb
    return LogRegModel(w=w, b=b, mu=mu, sd=sd, feature_names=tuple(feature_names))


# ── 순위표 조립 ─────────────────────────────────────────────────────────────
@dataclass
class RankingResult:
    ranking: list[SignalKey]          # 상위(신호 강함)부터
    universe_size: int                # 게이트 통과 후보 수 = Lift/축소율 분모 N
    version: str
    meta: dict = field(default_factory=dict)


def _require_columns(signals: pd.DataFrame, spec: VersionSpec) -> list[str]:
    """이 버전이 쓰는 피처 컬럼을 확인. 없으면 FeatureUnavailable."""
    cols = []
    missing = []
    for f in spec.features:
        col = FEATURE_COLUMNS[f]
        cols.append(col)
        if col not in signals.columns:
            missing.append((f, col))
    if missing:
        lines = [
            f"  · {f!r} → 컬럼 '{col}' 없음"
            + (f"  (출처: {FEATURE_SOURCE_HINT[f]})" if f in FEATURE_SOURCE_HINT else "")
            for f, col in missing
        ]
        raise FeatureUnavailable(
            f"{spec.name}는 다음 피처가 필요하지만 signals DF에 없다:\n"
            + "\n".join(lines)
            + "\n→ 상류에서 (ingredient, pt)별로 집계해 컬럼을 추가한 뒤 넘겨라. "
            "(v1/v2는 ic025만 있으면 지금 바로 실행 가능)"
        )
    return cols


def build_ranking(
    signals: pd.DataFrame,
    version: str = "v1",
    *,
    positives: Iterable[SignalKey] | None = None,
    negatives: Iterable[SignalKey] | None = None,
    label_excludes: Iterable[SignalKey] | None = None,
    gate: Gate | None = None,
    l2: float = 1.0,
    bg_neg_ratio: float = 0.0,
) -> RankingResult:
    """signals(fct_signal_scores) → 정렬된 순위표.

    signals        : 최소 컬럼 = ingredient, pt, ic025, a. v3+는 피처 컬럼도 필요.
    positives/negatives : 학습 결합(logreg)용 라벨. v3+에서만 사용.
    label_excludes : 시점(T) 이전에 이미 라벨에 있던 (성분,반응) 키 — v2+ 이진 제거.
                     None 이면 필터는 '통과'(경고). 실제 집합은 상류 Drugs@FDA 시점 라벨.
    gate           : 고정 게이트. None 이면 미적용(v1 '전수 완주'). 표준은 Gate.standard().
    bg_neg_ratio   : v3+ 학습에 배경 음성(라벨 없는 후보)을 양성 수 × ratio 개 추가. 0 = 안 씀.
    """
    if version not in VERSIONS:
        raise KeyError(f"알 수 없는 버전 {version!r} (가능: {list(VERSIONS)})")
    spec = VERSIONS[version]

    for base in (INGREDIENT_COL, REACTION_COL, IC025_COL, COUNT_COL):
        if base not in signals.columns:
            raise KeyError(f"signals DF에 필수 컬럼 '{base}' 없음")

    feature_cols = _require_columns(signals, spec)
    df = signals.copy()
    meta: dict = {"version": version, "note": spec.note}

    # 1) 게이트 (고정 임계값)
    n_before = len(df)
    if gate is not None:
        df = df[df[COUNT_COL] >= gate.a_min]
        if gate.ic025_min is not None:
            df = df[df[IC025_COL] > gate.ic025_min]
    meta["gate"] = {
        "applied": gate is not None,
        "a_min": gate.a_min if gate else None,
        "ic025_min": gate.ic025_min if gate else None,
        "kept": len(df), "dropped": n_before - len(df),
    }

    # 2) 후보 키
    df = df.reset_index(drop=True)
    keys = [make_key(i, r) for i, r in zip(df[INGREDIENT_COL], df[REACTION_COL])]

    # 3) 시점 라벨 필터 (v2+, 이진 제거)
    if spec.use_label_filter:
        if label_excludes is None:
            warnings.warn(
                f"{version}: 시점 라벨 필터가 켜졌지만 label_excludes 가 없어 "
                "아무것도 제거하지 않는다. 상류 Drugs@FDA 시점 라벨 집합을 넘겨라.",
                stacklevel=2,
            )
            excl: set[SignalKey] = set()
        else:
            excl = set(label_excludes)
        keep_mask = np.array([k not in excl for k in keys])
        df = df[keep_mask].reset_index(drop=True)
        keys = [k for k, m in zip(keys, keep_mask) if m]
        meta["label_filter"] = {"excluded_universe": len(excl),
                                 "removed": int((~keep_mask).sum())}

    universe_size = len(df)

    # 4) 결합 → 점수
    if spec.combiner == "ic025":
        scores = df[IC025_COL].to_numpy(dtype=float)
        meta["combiner"] = "ic025"
    elif spec.combiner == "ic025_log_a":
        # 증거량 가중: IC025 × log(a). a는 공출현 case 수(a≥1, 게이트 시 a≥3).
        a = df[COUNT_COL].to_numpy(dtype=float)
        scores = df[IC025_COL].to_numpy(dtype=float) * np.log(np.maximum(a, 1.0))
        meta["combiner"] = "ic025_log_a"
    else:
        scores, cmeta = _logreg_scores(
            df, keys, feature_cols, spec, positives, negatives, l2,
            bg_neg_ratio=bg_neg_ratio,
        )
        meta.update(cmeta)

    # 5) 정렬 (점수 내림차순, 동점은 ic025 보조키)
    order = np.lexsort((df[IC025_COL].to_numpy(dtype=float), scores))[::-1]
    ranking = [keys[i] for i in order]

    return RankingResult(ranking=ranking, universe_size=universe_size,
                         version=version, meta=meta)


def _logreg_scores(
    df: pd.DataFrame,
    keys: list[SignalKey],
    feature_cols: list[str],
    spec: VersionSpec,
    positives: Iterable[SignalKey] | None,
    negatives: Iterable[SignalKey] | None,
    l2: float,
    n_folds: int = 5,
    bg_neg_ratio: float = 0.0,
) -> tuple[np.ndarray, dict]:
    """학습 결합 점수 — 모든 후보를 cross-fit 으로 채점(in-sample 누수 + 채점 모델 불일치 방지).

    · 후보 전부(라벨 양성·음성·라벨 없음)를 k 겹으로 나눈다. 라벨은 층화, 라벨 없음은 등장 순서.
    · 겹 f 의 후보는 **겹 f 를 뺀 라벨로 학습한 같은 모델 하나**가 채점한다
      → 채점에 쓰는 양성을 그 양성으로 학습한 모델이 매기지 않고(OOF),
        라벨 있는 점과 없는 점이 **같은 모델**에서 점수를 받는다.
    · 겹마다 점수를 그 겹 안의 백분위로 바꾼다 — 겹 모델마다 절편이 조금씩 달라도
      겹 간 점수 수준 차이가 순위에 새지 않는다.

    (이전 구현은 라벨 점=겹 모델, 라벨 없는 점=전체 모델로 채점했다. 피처 정보가 약하면
    겹별 절편 차이가 순위를 결정해, 실데이터 v3에서 라벨 332개가 0~54위와 맨 끝에
    덩어리로 몰렸다 — 2026-11-19 faers-work 스모크.)

    bg_neg_ratio > 0 이면 라벨 없는 후보 중 양성 수 × ratio 개를 등간격으로 골라
    **배경 음성**(y=0)으로 학습에 추가한다(PU 방식). 기본 음성(FDA 검토 후 미조치)은
    그 자체가 강신호라 "신호 vs 배경"을 못 배우는 문제를 보완한다. 기본값 0 = 기존 라벨만.

    라벨 부족(양/음성 < 2) 시 ic025 정렬로 fallback.
    계수 보고(coef)는 전체 학습셋으로 적합한 모델 기준(해석용, 채점과 분리).
    """
    pos = set(positives or ())
    neg = set(negatives or ())
    X = df[feature_cols].to_numpy(dtype=float)
    X = np.nan_to_num(X, nan=0.0)

    y = np.full(len(keys), np.nan)
    for i, k in enumerate(keys):
        if k in pos:
            y[i] = 1.0
        elif k in neg:
            y[i] = 0.0
    labeled = ~np.isnan(y)
    pos_idx = np.where(y == 1.0)[0]
    neg_idx = np.where(y == 0.0)[0]
    n_pos, n_neg = len(pos_idx), len(neg_idx)

    # 배경 음성(선택): 라벨 없는 후보에서 등간격(결정적)으로 뽑아 y=0 학습 행으로 추가
    n_bg = 0
    if bg_neg_ratio > 0 and n_pos > 0:
        unl_idx = np.where(~labeled)[0]
        n_bg = min(int(round(n_pos * bg_neg_ratio)), len(unl_idx))
        if n_bg > 0:
            pick = unl_idx[np.linspace(0, len(unl_idx) - 1, n_bg).round().astype(int)]
            y[pick] = 0.0
            labeled = ~np.isnan(y)
            neg_idx = np.where(y == 0.0)[0]

    if n_pos < 2 or len(neg_idx) < 2:
        warnings.warn(
            f"{spec.name}: 후보 안에 학습 가능한 라벨이 부족하다 "
            f"(양성 {n_pos}, 음성 {n_neg}, 배경 음성 {n_bg}) → ic025 정렬로 fallback.",
            stacklevel=2,
        )
        return df[IC025_COL].to_numpy(dtype=float), {
            "combiner": "logreg->ic025_fallback",
            "n_pos_train": n_pos, "n_neg_train": n_neg,
        }

    # 전체 학습셋 모델: 계수 보고용(+ 학습 겹에 한 클래스가 없을 때만 대체 채점)
    full = fit_shrinkage_logreg(X[labeled], y[labeled], spec.features, l2=l2)

    # cross-fit 겹 배정 (결정적, 난수 없음): 양성·음성은 층화, 라벨 없음은 등장 순서로
    k = max(2, min(n_folds, n_pos, len(neg_idx)))
    fold = np.full(len(keys), -1, dtype=int)
    for j, idx in enumerate(pos_idx):
        fold[idx] = j % k
    for j, idx in enumerate(neg_idx):
        fold[idx] = j % k
    unl = np.where(~labeled)[0]
    fold[unl] = np.arange(len(unl)) % k

    scores = np.zeros(len(keys), dtype=float)
    oof_scored = 0
    folds_fallback = 0
    for f in range(k):
        test = fold == f
        train = labeled & (fold != f)
        ytr = y[train]
        if (ytr == 1).sum() < 1 or (ytr == 0).sum() < 1:
            m = full  # 학습 겹에 한 클래스가 없음(드묾) — 이 겹의 라벨 점은 OOF 아님
            folds_fallback += 1
        else:
            m = fit_shrinkage_logreg(X[train], ytr, spec.features, l2=l2)
            oof_scored += int((test & labeled).sum())
        # 겹 안 백분위 — 겹 모델 간 절편·스케일 차이 제거
        scores[test] = pd.Series(m.predict_proba(X[test])).rank(method="average", pct=True).to_numpy()

    return scores, {
        "combiner": "logreg",
        "scoring": "crossfit_fold_percentile",
        "n_pos_train": n_pos, "n_neg_train": n_neg, "n_bg_neg_train": n_bg,
        "oof_folds": k, "oof_scored": oof_scored, "folds_fallback": folds_fallback,
        "coef": full.coef_report(), "intercept": float(full.b),
    }
