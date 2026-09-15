"""scoring.audit_labelset — 감사 워크시트 구성 규칙."""

import pandas as pd

from scoring.audit_labelset import build_audit_sheet

# updated + no action 이 한 info 에 섞인 다제품 블록 + 단순 보류 블록
_MIXED = (
    "Updated The labeling for aripiprazole and quetiapine products was updated "
    "in 2025. FDA determined that no action was necessary for Primaquine."
)
_PENDING = "FDA is evaluating the need for regulatory action."


def _df():
    rows = [
        ("Abilify (aripiprazole)", "fecal incontinence", _MIXED, "양성"),
        ("Primaquine", "fecal incontinence", _MIXED, "음성"),
        ("DrugX", "rash", _PENDING, "보류"),
        ("DrugY", "nausea", _PENDING, "보류"),
        ("DrugZ", "fever", _PENDING, "보류"),
    ]
    return pd.DataFrame(
        [
            {"product": p, "signal": s, "info": i, "label3": l,
             "year": 2025, "q_start": "January", "quarter_label": "2025Q1",
             "source_url": "http://x"}
            for p, s, i, l in rows
        ]
    )


def test_mixed_becomes_required_and_shares_block():
    sheet = build_audit_sheet(_df(), sample_n=0)
    req = sheet[sheet["priority"] == "대조필수"]
    # 혼합 info 를 공유한 두 쌍이 대조필수로, 같은 block_id 로 묶인다
    assert set(req["product"]) == {"Abilify (aripiprazole)", "Primaquine"}
    assert req["block_id"].nunique() == 1
    assert (req["n_pairs_in_block"] == 2).all()


def test_sample_size_and_no_overlap_with_required():
    sheet = build_audit_sheet(_df(), sample_n=2, seed=1)
    assert (sheet["priority"] == "표본").sum() == 2
    # 표본은 비혼합(보류)에서만 뽑힌다 — 대조필수와 겹치지 않음
    smp = sheet[sheet["priority"] == "표본"]
    assert set(smp["auto_label"]) <= {"보류"}


def test_human_columns_blank_and_sorted_required_first():
    sheet = build_audit_sheet(_df(), sample_n=2, seed=1)
    assert (sheet[["human_label", "match", "note"]] == "").all().all()
    # audit_id 는 1..N 연속, 대조필수가 앞
    assert list(sheet["audit_id"]) == list(range(1, len(sheet) + 1))
    first_two = sheet.head(2)["priority"].tolist()
    assert first_two == ["대조필수", "대조필수"]
