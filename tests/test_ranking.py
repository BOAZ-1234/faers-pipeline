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
def _separable_signals(n_pos=30, n_neg=30, n_unl=300, seed=0):
    """양성은 death_frac 등이 높고 음성은 낮게, ic025 는 전원 같은 분포(못 가름).

    겹마다 수십~수백 점이 들어가는 크기라야 겹 안 백분위가 의미를 갖는다. 12행짜리
    `_signals()` 는 겹당 4점이라 백분위가 0.25 단위로 뭉개져, 행 순서에 따라 통과/실패가
    갈렸다(이전 구현도 무작위 행 순서 20회 중 16회 실패 — 리뷰 #79 대응 중 확인).
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    rows = []
    for kind, n, hi in (("pos", n_pos, True), ("neg", n_neg, False), ("unl", n_unl, None)):
        for i in range(n):
            if hi is None:
                f = rng.uniform(0.1, 0.6, 4)
            else:
                f = rng.uniform(0.75, 0.95, 4) if hi else rng.uniform(0.0, 0.08, 4)
            rows.append(dict(ingredient=f"{kind}drug{i}", pt=f"{kind}reac{i}",
                             ic025=rng.normal(1.0, 0.5), a=int(rng.integers(3, 50)),
                             death_frac=f[0], dechal_frac=f[1], rechal_frac=f[2], recency=f[3]))
    sig = pd.DataFrame(rows)
    pos = {make_key(f"posdrug{i}", f"posreac{i}") for i in range(n_pos)}
    neg = {make_key(f"negdrug{i}", f"negreac{i}") for i in range(n_neg)}
    return sig, pos, neg


@pytest.mark.parametrize("version", ["v3", "v4", "v5"])
def test_learned_versions_rank_positives_above_negatives(version):
    sig, positives, negatives = _separable_signals()
    rr = build_ranking(sig, version, positives=positives, negatives=negatives)
    pos_ranks = [rr.ranking.index(k) for k in positives]
    neg_ranks = [rr.ranking.index(k) for k in negatives]
    # death_frac 등 피처가 양성에서 높으므로 양성 전부가 음성 전부보다 앞(작음)이어야
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


# ── v3~v5: cross-fit 채점 — 라벨 점과 라벨 없는 점을 같은 모델로 ───────────────
def _noise_signals(n_pos=20, n_neg=10, n_unl=300, seed=0):
    """피처가 라벨과 무관한(정보 없는) 순위표 — 실데이터 v3 스모크(계수≈0) 재현용."""
    import numpy as np
    rng = np.random.default_rng(seed)
    n = n_pos + n_neg + n_unl
    names = ([f"p{i}" for i in range(n_pos)] + [f"n{i}" for i in range(n_neg)]
             + [f"u{i}" for i in range(n_unl)])
    sig = pd.DataFrame(dict(
        ingredient=names, pt=[f"r_{x}" for x in names],
        ic025=rng.normal(1.0, 0.5, n), a=rng.integers(3, 50, n),
        death_frac=rng.uniform(0, 1, n), dechal_frac=rng.uniform(0, 1, n),
        rechal_frac=rng.uniform(0, 1, n), recency=rng.uniform(0, 1, n),
    ))
    pos = {make_key(f"p{i}", f"r_p{i}") for i in range(n_pos)}
    neg = {make_key(f"n{i}", f"r_n{i}") for i in range(n_neg)}
    return sig, pos, neg


@pytest.mark.parametrize("version", ["v3", "v4", "v5"])
def test_labeled_keys_not_clustered_when_features_uninformative(version):
    """피처 정보가 없으면 라벨 점은 순위 전체에 흩어져야 한다(라벨 여부가 순위에 새지 않음).

    실데이터 v3 스모크에서 라벨 332개가 0~54위와 맨 끝에 몰린 현상의 성질 검사.
    합성 데이터로는 이전 구현에서도 재현되지 않아, 회귀 재현이 아니라 불변식 검사다.
    """
    sig, pos, neg = _noise_signals()
    rr = build_ranking(sig, version, positives=pos, negatives=neg)
    labeled = pos | neg
    n_lab = len(labeled)
    top = rr.ranking[:n_lab]
    bottom = rr.ranking[-n_lab:]
    # 무작위라면 상위/하위 n_lab 안의 라벨 기대값 ≈ n_lab²/N ≈ 2.7개
    assert sum(k in labeled for k in top) < n_lab // 2
    assert sum(k in labeled for k in bottom) < n_lab // 2


def test_crossfit_scores_every_candidate_out_of_fold():
    sig = _signals()
    positives, negatives = _labels()
    rr = build_ranking(sig, "v3", positives=positives, negatives=negatives)
    assert rr.meta["scoring"] == "crossfit_fold_percentile"
    assert rr.meta["folds_fallback"] == 0
    assert rr.meta["n_bg_neg_train"] == 0


def test_bg_neg_ratio_adds_background_negatives():
    sig, pos, neg = _noise_signals()
    rr = build_ranking(sig, "v3", positives=pos, negatives=neg, bg_neg_ratio=2.0)
    assert rr.meta["n_bg_neg_train"] == 40   # 양성 20 × 2
    assert rr.meta["n_neg_train"] == 10      # 원래 음성 수는 그대로 보고


def test_bg_neg_ratio_learns_signal_vs_background():
    """배경 음성을 넣으면, 양성이 배경보다 death_frac 이 높을 때 그걸 배운다."""
    sig, pos, neg = _noise_signals()
    is_pos = sig["ingredient"].str.startswith("p")
    sig.loc[is_pos, "death_frac"] = 0.95
    rr = build_ranking(sig, "v3", positives=pos, negatives=neg, bg_neg_ratio=3.0)
    assert rr.meta["coef"]["death_frac"] > 0
    pos_ranks = sorted(rr.ranking.index(k) for k in pos)
    assert pos_ranks[len(pos_ranks) // 2] < len(rr.ranking) // 4   # 양성 중앙값이 상위 25% 안


# ── v3~v5: 입력 행 순서와 무관 (리뷰 #79) ──────────────────────────────────────
@pytest.mark.parametrize("version,bg", [("v3", 0.0), ("v3", 3.0), ("v5", 3.0)])
def test_learned_ranking_invariant_to_row_order(version, bg):
    """같은 데이터를 다른 행 순서로 넘겨도 순위·계수가 같아야 한다.

    겹 배정·배경 음성 선택이 행 위치에 기대면, parquet 을 다시 저장/로드(ORDER BY 없음)
    하는 것만으로 Recall@K 가 바뀐다.
    """
    sig, pos, neg = _noise_signals(n_pos=40, n_neg=20, n_unl=1000)
    sig.loc[sig["ingredient"].str.startswith("p"), "death_frac"] += 0.3
    a = build_ranking(sig, version, positives=pos, negatives=neg, bg_neg_ratio=bg)
    for seed in (1, 2):
        shuffled = sig.sample(frac=1, random_state=seed)
        b = build_ranking(shuffled, version, positives=pos, negatives=neg, bg_neg_ratio=bg)
        assert b.ranking == a.ranking
        assert b.meta["coef"] == pytest.approx(a.meta["coef"])


def test_bg_neg_picks_are_unique_near_full_unlabeled():
    """배경 음성 수가 라벨 없는 후보 수에 가까워도 중복 없이 요청한 만큼 뽑힌다."""
    sig, pos, neg = _noise_signals(n_pos=20, n_neg=10, n_unl=300)
    rr = build_ranking(sig, "v3", positives=pos, negatives=neg, bg_neg_ratio=14.9)
    assert rr.meta["n_bg_neg_train"] == 298   # round(20 × 14.9)
    rr = build_ranking(sig, "v3", positives=pos, negatives=neg, bg_neg_ratio=100.0)
    assert rr.meta["n_bg_neg_train"] == 300   # 라벨 없는 후보 전부(상한)
