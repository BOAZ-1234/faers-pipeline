"""labelmap(확장 키 매핑) + pit(v2 시점 리드타임) 테스트 — 합성 데이터."""

from __future__ import annotations

import pandas as pd
import pytest

from scoring.keys import make_key
from scoring.labelmap import (
    build_eval_labelset,
    make_ingredient_resolver,
    make_reaction_resolver,
    vocab_from_signals,
)
from scoring.pit import LeadRow, detected_keys, leadtime, quarter_index, summarize
from scoring.ranking import Gate


# ── labelmap ─────────────────────────────────────────────────────────────────
def test_ingredient_resolver_paren_and_map():
    vocab = {"aspirin", "warfarin"}
    r = make_ingredient_resolver(vocab, drug_map={"bayer": "aspirin"})
    assert r("Aspirin (Bayer)") == "aspirin"   # 괄호 앞
    assert r("Warfarin") == "warfarin"          # 원문
    assert r("Bayer") == "aspirin"              # 사전 경유
    assert r("Unknownium") is None              # vocab 에 없음


def test_reaction_resolver_dict_and_split():
    vocab = {"headache", "bleeding", "nausea"}
    r = make_reaction_resolver(vocab, reaction_map={"big bleed": ["bleeding"]})
    assert r("big bleed") == {"bleeding"}                 # 사전
    assert r("Headache") == {"headache"}                  # vocab 직매칭
    assert r("Bleeding, and nausea") == {"bleeding", "nausea"}  # 쪼개기 폴백
    assert r("zzz") == set()


def _gold():
    rows = [
        ("Aspirin (Bayer)", "Headache", "양성", 2015, "January", "Jan-Mar 2015"),
        ("Warfarin", "Bleeding, and nausea", "양성", 2016, "January", "Jan-Mar 2016"),
        ("Aspirin (Bayer)", "Nausea", "음성", 2015, "April", "Apr-Jun 2015"),
        ("Unknownium", "Headache", "양성", 2015, "January", "Jan-Mar 2015"),
    ]
    return pd.DataFrame(rows, columns=["product", "signal", "label", "year",
                                       "q_start", "quarter_label"])


def test_build_eval_labelset_expands_and_counts_coverage():
    ing_vocab = {"aspirin", "warfarin"}
    pt_vocab = {"headache", "bleeding", "nausea"}
    ls = build_eval_labelset(
        _gold(), ing_vocab=ing_vocab, pt_vocab=pt_vocab,
        reaction_map={"bleeding, and nausea": ["bleeding", "nausea"]},
    )
    # 다중 PT 확장: warfarin → bleeding, nausea 두 키
    assert make_key("aspirin", "headache") in ls.positives
    assert make_key("warfarin", "bleeding") in ls.positives
    assert make_key("warfarin", "nausea") in ls.positives
    # Unknownium 은 성분 vocab 에 없어 키 생성 실패 → 커버리지에서 빠짐
    assert ls.coverage["pos_pairs"] == 3       # 양성 pair 3개
    assert ls.coverage["pos_keyed"] == 2       # 그중 2개만 키 생성
    assert 0.6 < ls.coverage["pos_coverage"] < 0.7


def test_vocab_from_signals():
    df = pd.DataFrame({"ingredient": ["Aspirin", "WARFARIN"], "pt": ["Headache", "Nausea"]})
    ing, pt = vocab_from_signals(df)
    assert ing == {"aspirin", "warfarin"}
    assert pt == {"headache", "nausea"}


# ── pit (v2) ─────────────────────────────────────────────────────────────────
def test_quarter_index_sortable():
    assert quarter_index(2015, "January") < quarter_index(2015, "July")
    assert quarter_index(2015, "October") < quarter_index(2016, "January")


def test_detected_keys_applies_gate():
    df = pd.DataFrame({
        "ingredient": ["a", "b", "c"],
        "pt": ["x", "y", "z"],
        "a": [10, 1, 10],         # b 는 a<3 탈락
        "ic025": [1.0, 1.0, -0.5],  # c 는 ic025<=0 탈락
    })
    det = detected_keys(df, gate=Gate.standard())
    assert det == {make_key("a", "x")}


def test_leadtime_and_summary():
    k1, k2, k3 = make_key("a", "x"), make_key("b", "y"), make_key("c", "z")
    positives_asof = {k1: 20153, k2: 20162, k3: 20160}
    snapshots = {
        20151: {k1},          # k1 은 20151 에 이미 떴다 (조치 20153 → 리드 2)
        20162: {k1, k2},      # k2 는 조치(20162)와 동시 → 앞선 게 아님
    }
    rows = leadtime(positives_asof, snapshots)
    by = {r.key: r for r in rows}
    assert by[k1].first_detected == 20151 and by[k1].detected_before and by[k1].lead == 2
    assert by[k2].first_detected == 20162 and not by[k2].detected_before  # 동시=앞선 것 아님
    assert by[k3].first_detected is None and not by[k3].detected_before    # 아예 안 뜸

    s = summarize(rows)
    assert s["n_positives"] == 3
    assert s["detected_before_action"] == 1
    assert s["median_lead"] == 2
