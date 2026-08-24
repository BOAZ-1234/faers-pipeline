"""정답지 라벨셋 구성 + 코호트 분할 테스트."""

import pandas as pd
import pytest

from scoring import ground_truth as gt
from scoring.keys import make_key


def _df(rows):
    return pd.DataFrame(
        rows, columns=["product", "signal", "info", "quarter_label", "year", "q_start"]
    )


def test_terminal_label_prefers_resolved():
    # 같은 쌍이 보류→양성으로 바뀌면 종결 라벨은 양성
    df = _df([
        ["DrugA", "Rash", "is evaluating", "Q1 2023", 2023, "January"],   # 보류
        ["DrugA", "Rash", "label was updated", "Q3 2023", 2023, "July"],  # 양성
    ])
    df["label"] = ["보류", "양성"]
    pairs = gt.terminal_pairs(df)
    assert len(pairs) == 1
    assert pairs.iloc[0]["label"] == "양성"
    assert pairs.iloc[0]["first_year"] == 2023


def test_cutoff_excludes_low_resolution_year():
    # 2023: 판정완료율 높음 / 2025: 대부분 보류 → 컷오프 2023
    rows, labels = [], []
    for i in range(8):
        rows.append([f"D{i}", "Sig", "x", "Q1", 2023, "January"])
        labels.append("양성" if i < 7 else "보류")  # 7/8 = 88%
    for i in range(8):
        rows.append([f"E{i}", "Sig", "x", "Q1", 2025, "January"])
        labels.append("보류" if i < 7 else "양성")  # 1/8 = 12%
    df = _df(rows)
    df["label"] = labels
    pairs = gt.terminal_pairs(df)
    assert gt.choose_cutoff_year(pairs, min_resolution_rate=0.75) == 2023


def test_build_labelset_splits_and_keys():
    df = _df([
        ["Aspirin", "Nausea", "updated", "Q1", 2023, "January"],   # 양성
        ["Warfarin", "Bleeding", "no action", "Q1", 2023, "January"],  # 음성
        ["NewDrug", "Headache", "is evaluating", "Q1", 2025, "January"],  # 보류
    ])
    df["label"] = ["양성", "음성", "보류"]
    ls = gt.build_labelset(df, min_resolution_rate=0.5)
    assert make_key("Aspirin", "Nausea") in ls.positives
    assert make_key("Warfarin", "Bleeding") in ls.negatives
    assert make_key("NewDrug", "Headache") in ls.sealed


def test_build_labelset_with_mappers():
    # 매퍼 주입: 브랜드→성분, 표기→PT 정규화가 붙었을 때 키가 맞는지
    df = _df([["Tylenol", "Liver injury", "updated", "Q1", 2023, "January"]])
    df["label"] = ["양성"]
    brand2ing = {"Tylenol": "acetaminophen"}
    sig2pt = {"Liver injury": "Hepatic failure"}
    ls = gt.build_labelset(
        df,
        ingredient_of=lambda p: brand2ing.get(p, p),
        reaction_of=lambda s: sig2pt.get(s, s),
        min_resolution_rate=0.5,
    )
    assert make_key("acetaminophen", "Hepatic failure") in ls.positives
    assert make_key("Tylenol", "Liver injury") not in ls.positives
