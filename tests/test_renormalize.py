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


def test_ingredient_key_maps_to_single_ingredient_not_combo_product(tmp_path):
    """성분명 키는 그 성분 하나를 가리켜야 한다. 사전에서 그 성분 행이 복합제 제품에서 왔더라도
    제품의 ingredient_set(묶음)을 물려받으면 안 된다(PR#43 2단계에서 실제로 난 오매칭 유형)."""
    path = tmp_path / "map.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["medicinalproduct", "ingredient_norm", "ingredient_set", "unii",
                    "source", "method", "confidence", "dictionary_version"])
        combo = "amlodipine|hydrochlorothiazide|olmesartan medoxomil"
        for ingr in ("AMLODIPINE", "HYDROCHLOROTHIAZIDE", "OLMESARTAN MEDOXOMIL"):
            w.writerow(["TRIBENZOR", ingr, combo, "", "", "", "", ""])
    index = renormalize.load_key_index(path)
    assert index["AMLODIPINE"] == frozenset({"amlodipine"})
    assert index["TRIBENZOR"] == frozenset({combo})
    res = renormalize.resolve("AMLODIPINE 5 MG TABLET", index)
    assert (res.status, res.ingredient_set) == ("RESOLVED", "amlodipine")
    res = renormalize.resolve("TRIBENZOR 40/5/12.5 MG", index)
    assert (res.status, res.ingredient_set) == ("RESOLVED", combo)


def test_form_only_paren_is_not_a_query():
    # "(NASAL SOLUTION)" → 정규화하면 "NASAL"만 남아 같은 이름의 엉뚱한 제품에 붙던 것
    queries = [q for q, _ in renormalize.candidate_queries("IPRATROPIUM BROMIDE(NASAL SOLUTION) .03%")]
    assert "NASAL" not in queries
    assert "IPRATROPIUM" in queries
    # 성분이 든 괄호는 그대로 후보
    assert "ZOLEDRONIC ACID" in [q for q, _ in renormalize.candidate_queries("ZOMETA (ZOLEDRONIC ACID)")]


def test_strength_requires_unit():
    # 숫자+단위만 용량으로 본다. COVID-19·B12 같은 이름 속 숫자에서 자르지 않는다.
    assert renormalize.STRENGTH.search("COVID-19 VACCINE") is None
    assert renormalize.STRENGTH.search("VITAMIN B12") is None
    assert renormalize.STRENGTH.search("LUPRON DEPOT 3.75 MG").group().strip() == "3.75 MG"
    # 복합제 다중 용량은 첫 숫자부터 한 덩어리
    assert renormalize.STRENGTH.search("TRIBENZOR 40/5/12.5 MG").group() == "40/5/12.5 MG"
    assert renormalize.STRENGTH.search("HYCODAN 5-1.5 MG/5ML").group().startswith("5-1.5 MG")
    # 앞 0 생략 소수, 이름·제형에 점으로 붙은 용량
    assert renormalize.STRENGTH.search("LORAZEPAM .5 MG").group() == ".5 MG"
    assert renormalize.STRENGTH.search("ALPRAZOLAM.25MG") is not None
    assert renormalize.STRENGTH.search("DIGOXIN TAB.125MG") is not None


def _write_map(path, rows, version=renormalize.DICTIONARY_VERSION):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["medicinalproduct", "ingredient_norm", "ingredient_set", "unii",
                    "source", "method", "confidence", "dictionary_version"])
        for p, i, s in rows:
            w.writerow([p, i, s, "", "", "", "", version])


def test_make_dict_lookup_only_resolved(tmp_path):
    # triage.load_dict_lookup 자리에 꽂는 name→bool: RESOLVED만 True, CONFLICT는 False(UNKNOWN에 남음)
    path = tmp_path / "map.csv"
    _write_map(path, [("HUMIRA", "ADALIMUMAB", "adalimumab"),
                      ("LEXAPRO", "ESCITALOPRAM", "escitalopram"),
                      ("ESCITALOPRAM OXALATE", "ESCITALOPRAM OXALATE", "escitalopram oxalate")])
    lookup = renormalize.make_dict_lookup(path)
    assert lookup("HUMIRA 40 MG/0.8 ML PEN") is True
    assert lookup("LEXAPRO (ESCITALOPRAM OXALATE)") is False
    assert lookup("NOT A DRUG") is False


# --- 사전 위치·버전 검사 (팀 합의: silver/drug_dict/drug_ingredient_map.csv) ---------------
def test_default_map_path_is_silver_drug_dict():
    assert renormalize.DEFAULT_MAP == DRUG_DICT / "drug_ingredient_map.csv"


def test_missing_map_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="drug_ingredient_map.csv"):
        renormalize.make_dict_lookup(tmp_path / "nope.csv")


@pytest.mark.parametrize("version", ["d-2025Q1", ""])
def test_stale_or_unversioned_map_raises(tmp_path, version):
    path = tmp_path / "map.csv"
    _write_map(path, [("HUMIRA", "ADALIMUMAB", "adalimumab")], version=version)
    with pytest.raises(ValueError, match="사전 버전 불일치"):
        renormalize.make_dict_lookup(path)


def test_mixed_versions_raise(tmp_path):
    path = tmp_path / "map.csv"
    _write_map(path, [("HUMIRA", "ADALIMUMAB", "adalimumab")])
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["ZOMETA", "ZOLEDRONIC ACID", "zoledronic acid", "", "", "", "", "d-2025Q1"])
    with pytest.raises(ValueError, match="d-2025Q1"):
        renormalize.check_map(path)
