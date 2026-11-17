"""순위표 버전 사다리(v1~v5) 테스트 — 합성 데이터로 배관·결합 검증.

실데이터(fct_signal_scores) 없이도 v1~v5 가 돌고, 게이트·라벨필터·학습결합이
의도대로 동작하는지 본다.
"""

from __future__ import annotations

import warnings

import pandas as pd
import pytest

from scoring import make_key, score
from scoring.ranking import (
    FeatureUnavailable,
    Gate,
    VERSIONS,
    build_ranking,
    fit_shrinkage_logreg,
)


def _signals() -> pd.DataFrame:
    """양성 3 + 음성 3 + 잡음 6. 양성은 death_frac 이 높게 설계(피처가 신호를 가름).

    ic025 는 일부러 양/음성에서 비슷하게 둬서, v1(ic025) 만으로는 못 가르고
    v3(+death_frac) 에서 가려지는지 본다.
    """
    rows = []
    # 양성: ic025 중간, death_frac 높음
    for i in range(3):
        rows.append(dict(ingredient=f"posdrug{i}", pt=f"posreac{i}",
                         ic025=1.0 + 0.01 * i, a=10,
                         death_frac=0.8, dechal_frac=0.7, rechal_frac=0.6, recency=0.9))
    # 음성: ic025 중간(양성과 겹침), death_frac 낮음
    for i in range(3):
        rows.append(dict(ingredient=f"negdrug{i}", pt=f"negreac{i}",
                         ic025=1.0 + 0.01 * i, a=10,
                         death_frac=0.05, dechal_frac=0.1, rechal_frac=0.05, recency=0.2))
    # 잡음: ic025 낮고 a 작음(게이트 대상 일부)
    for i in range(6):
        rows.append(dict(ingredient=f"noise{i}", pt=f"noisereac{i}",
                         ic025=-0.5 + 0.1 * i, a=1 if i < 3 else 5,
                         death_frac=0.1, dechal_frac=0.1, rechal_frac=0.1, recency=0.5))
    return pd.DataFrame(rows)


def _labels():
    positives = {make_key(f"posdrug{i}", f"posreac{i}") for i in range(3)}
    negatives = {make_key(f"negdrug{i}", f"negreac{i}") for i in range(3)}
    return positives, negatives


# ── v1: IC025 단독 ──────────────────────────────────────────────────────────
def test_v1_sorts_by_ic025_desc():
    sig = _signals()
    rr = build_ranking(sig, "v1")
    assert rr.version == "v1"
    assert rr.universe_size == len(sig)  # 게이트 없음 → 전수
    ic025 = {make_key(r.ingredient, r.pt): r.ic025 for r in sig.itertuples()}
    seq = [ic025[k] for k in rr.ranking]
    assert seq == sorted(seq, reverse=True)  # 내림차순


def test_v1_runs_end_to_end_scoring():
    sig = _signals()
    positives, _ = _labels()
    rr = build_ranking(sig, "v1")
    card = score(rr.ranking, positives, universe_size=rr.universe_size, ks=(3, 6))
    assert card.n_positives_scorable == 3      # 양성 전부 후보에 존재
    assert card.row(6).hits == 3               # 상위 6 안에 양성 3 전부(ic025 상위)


# ── 게이트 ──────────────────────────────────────────────────────────────────
def test_gate_drops_low_count_and_ic025():
    sig = _signals()
    rr = build_ranking(sig, "v1", gate=Gate.standard())  # a>=3 AND ic025>0
    # a=1 잡음 3개 + ic025<=0 잡음 제거됨
    assert rr.universe_size < len(sig)
    assert rr.meta["gate"]["applied"] is True
    for k in rr.ranking:
        assert "noise0" not in k.ingredient  # a=1 잡음은 빠짐


# ── v2: 시점 라벨 필터(이진 제거) ───────────────────────────────────────────
def test_v2_label_filter_removes_pairs():
    sig = _signals()
    positives, negatives = _labels()
    exclude = {make_key("posdrug0", "posreac0")}  # 이미 라벨에 있던 쌍 가정
    rr = build_ranking(sig, "v2", positives=positives, negatives=negatives,
                       label_excludes=exclude)
    assert make_key("posdrug0", "posreac0") not in rr.ranking
    assert rr.meta["label_filter"]["removed"] == 1


def test_v2_warns_when_no_excludes():
    sig = _signals()
    with pytest.warns(UserWarning, match="label_excludes"):
        build_ranking(sig, "v2")


# ── v3~v5: 학습 결합 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("version", ["v3", "v4", "v5"])
def test_learned_versions_rank_positives_above_negatives(version):
    sig = _signals()
    positives, negatives = _labels()
    rr = build_ranking(sig, version, positives=positives, negatives=negatives)
    pos_ranks = [rr.ranking.index(k) for k in positives]
    neg_ranks = [rr.ranking.index(k) for k in negatives]
    # death_frac 등 피처가 양성에서 높으므로 평균 순위가 음성보다 앞(작음)이어야
    assert max(pos_ranks) < min(neg_ranks)
    assert rr.meta["combiner"] == "logreg"
    assert set(rr.meta["coef"]) == set(VERSIONS[version].features)


def test_v3_missing_feature_column_raises():
    sig = _signals().drop(columns=["death_frac"])
    positives, negatives = _labels()
    with pytest.raises(FeatureUnavailable, match="death_frac"):
        build_ranking(sig, "v3", positives=positives, negatives=negatives)


def test_logreg_fallback_when_labels_insufficient():
    sig = _signals()
    # 음성 라벨을 안 주면 학습 불가 → ic025 fallback
    positives, _ = _labels()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rr = build_ranking(sig, "v3", positives=positives, negatives=set())
    assert rr.meta["combiner"] == "logreg->ic025_fallback"


# ── 로지스틱 회귀 단위 검증 ─────────────────────────────────────────────────
def test_logreg_separates_linearly_separable():
    import numpy as np
    X = np.array([[0.0], [0.1], [0.9], [1.0]])
    y = np.array([0.0, 0.0, 1.0, 1.0])
    model = fit_shrinkage_logreg(X, y, ["f"], l2=0.01)
    p = model.predict_proba(X)
    assert p[0] < 0.5 < p[-1]
    assert p[-1] > p[0]


def test_all_versions_registered():
    assert list(VERSIONS) == ["v1", "v1e", "v2", "v3", "v4", "v5"]


# ── v1e: IC025 × log(a) (증거량 가중, 헤드라인 재현) ─────────────────────────
def test_v1e_weights_ic025_by_log_count():
    import numpy as np
    sig = _signals()
    rr = build_ranking(sig, "v1e")
    assert rr.meta["combiner"] == "ic025_log_a"
    assert rr.universe_size == len(sig)
    expected = {
        make_key(r.ingredient, r.pt): r.ic025 * np.log(max(r.a, 1))
        for r in sig.itertuples()
    }
    seq = [expected[k] for k in rr.ranking]
    assert seq == sorted(seq, reverse=True)  # IC025×log(a) 내림차순


def test_v1e_no_leakage_without_labels():
    # 학습이 없으므로 positives/negatives 없이도 동일 점수 (누수 원천 차단)
    sig = _signals()
    a = build_ranking(sig, "v1e").ranking
    pos, neg = _labels()
    b = build_ranking(sig, "v1e", positives=pos, negatives=neg).ranking
    assert a == b


# ── v3~v5: OOF 채점(in-sample 누수 방지) ────────────────────────────────────
def test_learned_versions_use_oof_scoring():
    sig = _signals()
    positives, negatives = _labels()
    rr = build_ranking(sig, "v3", positives=positives, negatives=negatives)
    # 라벨된 6점(양성3+음성3)은 OOF로 채점되어야 한다
    assert rr.meta["combiner"] == "logreg"
    assert rr.meta["oof_folds"] >= 2
    assert rr.meta["oof_scored"] == 6
