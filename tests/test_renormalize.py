"""silver/drug_dict/renormalize.py (BRAND_DOSE 재정규화) 단위 테스트.

핵심은 두 가지다.
  1) 브랜드 뒤에 용량·괄호·기기명이 붙어 1단계에서 못 찾은 것을 **찾는가**
  2) 복합제·여러 제품이 섞인 표기를 **한 성분으로 잘못 붙이지 않는가** (precision 우선)
2)는 잔여 실측(unmatched_for_stage3, 2026-10-01 스냅샷)에서 실제로 오답이 났던 사례를 고정한다.
사전은 실데이터 대신 작은 픽스처 CSV를 tmp_path에 만들어 쓴다(S3·릴리즈 파일 불필요).
"""
import csv
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DRUG_DICT = REPO_ROOT / "silver" / "drug_dict"
if str(DRUG_DICT) not in sys.path:
    sys.path.insert(0, str(DRUG_DICT))

import renormalize  # noqa: E402

# (medicinalproduct, ingredient_norm, ingredient_set) — build_map 출력과 같은 컬럼
FIXTURE_ROWS = [
    ("HUMIRA", "ADALIMUMAB", "adalimumab"),
    ("OXYCONTIN", "OXYCODONE", "oxycodone"),
    ("ZOMETA", "ZOLEDRONIC ACID", "zoledronic acid"),
    ("SYMBICORT", "BUDESONIDE", "budesonide|formoterol"),
    ("SYMBICORT", "FORMOTEROL", "budesonide|formoterol"),
    ("CLOZARIL", "CLOZAPINE", "clozapine"),
    ("LEPONEX", "CLOZAPINE", "clozapine"),
    ("GILENYA", "FINGOLIMOD", "fingolimod"),
    ("ALLEGRA-D", "FEXOFENADINE", "fexofenadine|pseudoephedrine"),
    ("ALLEGRA-D", "PSEUDOEPHEDRINE", "fexofenadine|pseudoephedrine"),
    ("VICODIN", "HYDROCODONE BITARTRATE", "acetaminophen|hydrocodone"),
    ("HYDROCODONE BITARTRATE", "HYDROCODONE BITARTRATE", "hydrocodone"),
    ("BUTALBITAL", "BUTALBITAL", "butalbital"),
    ("ACETAMINOPHEN", "ACETAMINOPHEN", "acetaminophen"),
    ("LEXAPRO", "ESCITALOPRAM", "escitalopram"),
    ("ESCITALOPRAM OXALATE", "ESCITALOPRAM OXALATE", "escitalopram oxalate"),
    ("LANTUS", "INSULIN GLARGINE", "insulin glargine"),
    ("ISOFLURANE", "ISOFLURANE", "isoflurane"),
]


@pytest.fixture(scope="module")
def index(tmp_path_factory):
    path = tmp_path_factory.mktemp("dict") / "drug_ingredient_map.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["medicinalproduct", "ingredient_norm", "ingredient_set", "unii",
                    "source", "method", "confidence", "dictionary_version"])
        for p, i, s in FIXTURE_ROWS:
            w.writerow([p, i, s, "", "fixture", "사전", "", "test"])
    return renormalize.load_key_index(path)


# --- 1) 찾아야 하는 것 -----------------------------------------------------------
@pytest.mark.parametrize("name,ingredient_set,rule", [
    ("HUMIRA 40 MG/0.8 ML PEN", "adalimumab", "cut_strength"),          # 용량 뒤 기기명
    ("HUMIRA 40 MG/ 0.8 ML PRE-FILLED SYRINGE", "adalimumab", "cut_strength"),
    ("OXYCONTIN (NDA 22-272)", "oxycodone", "drop_paren"),              # 괄호 속 비성분
    ("ZOMETA (ZOLEDRONIC ACID)", "zoledronic acid", "drop_paren"),      # 괄호 밖·안이 같은 성분
    ("SYMBICORT PMDI", "budesonide|formoterol", "device_tail"),         # 복합제 브랜드는 묶음 그대로
    ("HUMIRA PEN", "adalimumab", "device_tail"),
    ("LEPONEX / CLOZARIL", "clozapine", "slash_alt"),                   # 대체명, 양쪽 같은 성분
    ("GILENYA (FINGOLIMOD) CAPSULE, 0.5MG", "fingolimod", "drop_paren"),
    ("ALLEGRA-D, 24 HR", "fexofenadine|pseudoephedrine", "cut_comma"),  # 쉼표 뒤가 설명뿐
    ("FORANE (ISOFLURANE, USP)", "isoflurane", "paren_list"),           # USP는 빈 조각이라 무시
])
def test_resolves(index, name, ingredient_set, rule):
    res = renormalize.resolve(name, index)
    assert res.status == "RESOLVED"
    assert res.ingredient_set == ingredient_set
    assert res.rule == rule


# --- 2) 한 성분으로 붙이면 안 되는 것 (실측 오답 사례) -------------------------------
@pytest.mark.parametrize("name,rule", [
    # 괄호 안 복합 성분 중 일부(PARACETAMOL)가 사전에 없음 → hydrocodone 단일로 붙던 것
    ("HYDROCODONE BITARTRATE (HYDROCODONE BITARTRATE, PARACETAMOL)", "paren_list"),
    # 괄호 밖 연결어 → 용량 앞 한 성분만 떼어 붙이지 않음
    ("ISOFLURANE 2% WITH OXYGEN", "combo_marker"),
    # " / " 한쪽만 사전에 있음 → 복합제 일부일 수 있음
    ("CLOZARIL / UNKNOWNBRAND", "slash_partial"),
    # 후보끼리 성분 묶음이 다름(사전 염 표기 불일치) → LLM이 판정
    ("LEXAPRO (ESCITALOPRAM OXALATE)", "multi_set"),
])
def test_conflict_not_resolved(index, name, rule):
    res = renormalize.resolve(name, index)
    assert res.status == "CONFLICT"
    assert res.rule == rule


@pytest.mark.parametrize("name", [
    "BUTALBITAL, ACETAMINOPHEN + CAFFEINE",  # 쉼표 뒤가 성분 → cut_comma 안 씀(butalbital 단일로 붙던 것)
    "SOLOSTAR",              # 기기명 자체가 이름 — 한 토큰은 떼지 않는다
    "DIANEAL PD2",           # 숫자 앞이 영문자(PD2)면 용량으로 보지 않음
    "ACETAMINOPHEN\\CODEINE",  # "\" 복합제는 1단계 조합 exact-match 영역
    "",
])
def test_miss(index, name):
    assert renormalize.resolve(name, index).status == "MISS"


def test_strength_requires_unit():
    # 숫자+단위만 용량으로 본다. COVID-19·B12 같은 이름 속 숫자에서 자르지 않는다.
    assert renormalize.STRENGTH.search("COVID-19 VACCINE") is None
    assert renormalize.STRENGTH.search("VITAMIN B12") is None
    assert renormalize.STRENGTH.search("LUPRON DEPOT 3.75 MG").group().strip() == "3.75 MG"


def test_make_dict_lookup_only_resolved(tmp_path):
    # triage.load_dict_lookup 자리에 꽂는 name→bool: RESOLVED만 True, CONFLICT는 False(UNKNOWN에 남음)
    path = tmp_path / "map.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["medicinalproduct", "ingredient_norm", "ingredient_set", "unii",
                    "source", "method", "confidence", "dictionary_version"])
        w.writerow(["HUMIRA", "ADALIMUMAB", "adalimumab", "", "", "", "", ""])
        w.writerow(["LEXAPRO", "ESCITALOPRAM", "escitalopram", "", "", "", "", ""])
        w.writerow(["ESCITALOPRAM OXALATE", "ESCITALOPRAM OXALATE", "escitalopram oxalate", "", "", "", "", ""])
    lookup = renormalize.make_dict_lookup(path)
    assert lookup("HUMIRA 40 MG/0.8 ML PEN") is True
    assert lookup("LEXAPRO (ESCITALOPRAM OXALATE)") is False
    assert lookup("NOT A DRUG") is False
