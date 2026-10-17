"""silver/drug_dict/triage.py (3단계 전 잔여 triage) 단위 테스트.

핵심은 OUT_OF_SCOPE 판정이 **진짜 약을 잘못 버리지 않는지**(false exclusion)이다.
잔여 감사(2026-10-05)에서 나온 경계 사례들을 고정한다. triage는 similarity(rapidfuzz)를
import하므로 없으면 skip한다.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DRUG_DICT = REPO_ROOT / "silver" / "drug_dict"
if str(DRUG_DICT) not in sys.path:
    sys.path.insert(0, str(DRUG_DICT))

triage = pytest.importorskip("triage", reason="rapidfuzz 필요 (pip install rapidfuzz)")


# --- OUT_OF_SCOPE로 제외돼야 하는 것 (붙일 성분 자체가 없음) ---
@pytest.mark.parametrize("name,reason", [
    ("VITAMINS", "prod_ai_exclude"),           # 기존 PROD_AI_EXCLUDE
    ("UNSPECIFIED INGREDIENT", "prod_ai_exclude"),
    ("INSULIN NOS", "NOS"),
    ("COVID-19 VACCINE NOS", "NOS"),
    ("EYE DROPS", "class/form"),
    ("ANTIHISTAMINES", "class/form"),
    ("MULTIVITAMINS", "vitamin_class"),        # 비특정 비타민 (감사 반영)
    (r"MINERALS\VITAMINS", "vitamin_class"),
    ("PRENATAL VITAMINS", "vitamin_class"),
    ("MULTIPLE VITAMINS", "vitamin_class"),
    ("VITAMIN B", "prod_ai_exclude"),          # 비특정 B군은 여전히 제외 (#48 리뷰 반영 후에도)
    ("PROACTIV MD DEEP CLEANSING FACE WASH", "cosmetic"),   # 명백 화장품
    ("PROACTIV GREEN TEA MOISTURIZER", "cosmetic"),
    ("PROACTIV EYE BRIGHTENING SERUM", "cosmetic"),         # SERUM 아닌 EYE BRIGHTENING으로
])
def test_out_of_scope(name, reason):
    assert triage.out_of_scope_reason(name) == reason


# --- 보호돼야 하는 것 (특정 성분/진짜 약 → 절대 제외 금지) ---
@pytest.mark.parametrize("name", [
    "ACETAMINOPHEN", "ASPIRIN", "HUMIRA", "KLACID", "VINBLASTINE",
    "NOSCAPINE",                               # "NOS" 들어있지만 단어경계로 보호
    "VITAMIN D (UNSPECIFIED)",                 # 특정 비타민 = 콜레칼시페롤
    "VITAMIN C", "VITAMIN B12", "D3 VITAMIN",
    "CALCIUM WITH VITAMIN D [CALCIUM CARBONATE;COLECALCIFEROL]",  # 대괄호 성분
    "MULTIVITAMIN IRON",                       # 특정 미네랄 섞임
    "MULTIVITAMIN [ASCORBIC ACID;BIOTIN]",     # 대괄호 성분 리스트
    "MIRACLE MOUTHWASH",                       # 조제약 (약용 가능 → 보류)
    "KETOCONAZOLE SHAMPOO",                    # 약용 샴푸
    "IMMUNE SERUM GLOBULIN",                   # 의학 혈청 (SERUM 오탐 방지)
    "ANTI-THYMOCYTE SERUM",
    # --- #48 리뷰 반영: 특정 비타민이 구분자로 번호와 떨어져도 보호 ---
    "VITAMIN B-12", "VITAMIN B 12", "VITAMIN B 2",   # normalize가 숫자 떼어 'VITAMIN B'로 안 떨어지게
    # --- #48 리뷰 반영: NOS 섞인 성분 나열에서 특정 부품 보호 ---
    "ESTROGEN NOS;METHYLTESTOSTERONE",         # METHYLTESTOSTERONE은 특정 성분
    "CALCIUM CITRATE;VITAMIN D NOS",           # CALCIUM CITRATE / VITAMIN D 특정
    "CENTRUM SILVER [ASCORBIC ACID;CALCIUM;MINERALS NOS]",  # 대괄호 성분 리스트
    # --- #48 리뷰 반영: 화장품 키워드라도 활성성분 있으면 보호 ---
    "PROACTIV CLEANSER WITH BENZOYL PEROXIDE 1%",
    "PROACTIV MOISTURIZER SPF 30",
])
def test_protected(name):
    assert triage.out_of_scope_reason(name) is None


@pytest.fixture
def synthetic_dict_map(tmp_path):
    """build_map.py 출력 스키마의 작은 사전 (BRAND_DOSE 재조회용)."""
    import csv
    p = tmp_path / "map.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["medicinalproduct", "ingredient_norm", "ingredient_set", "unii",
                    "source", "method", "confidence", "dictionary_version"])
        w.writerow(["HUMIRA", "ADALIMUMAB", "adalimumab", "", "t", "사전", "", "d-test"])
        w.writerow(["ASPIRIN", "ASPIRIN", "aspirin", "", "t", "사전", "", "d-test"])
    return p


def test_brand_dose_routed_via_renormalize(synthetic_dict_map):
    """triage의 BRAND_DOSE가 renormalize.make_dict_lookup에 배선됐는지(#50 연결).
    'HUMIRA 40 MG/0.8 ML PEN'은 1단계 normalize로는 안 잡히지만(끝 토큰만 떼다 멈춤)
    renormalize가 용량 뒤 기기명까지 잘라 HUMIRA로 재조회 → BRAND_DOSE. 옛 평이 재조회
    였다면 0이 나와 UNKNOWN(=Z, LLM 대상)으로 샜다."""
    lookup = triage.make_dict_lookup(synthetic_dict_map)
    rows = [("HUMIRA 40 MG/0.8 ML PEN", 300), ("ZZZQ NONEXISTENT", 10), ("EYE DROPS", 40)]
    buckets, _ = triage.triage(rows, lookup)
    assert [n for n, _ in buckets["BRAND_DOSE"]] == ["HUMIRA 40 MG/0.8 ML PEN"]
    assert ("ZZZQ NONEXISTENT", 10) in buckets["UNKNOWN"]       # 미지 → LLM 대상
    assert ("EYE DROPS", 40) in buckets["OUT_OF_SCOPE"]         # class/form은 그대로


def test_cutoff_curve_monotonic():
    """컷오프가 커지면 LLM 호출 수·커버 신고는 단조 감소해야 한다."""
    unknown = [("A", 1), ("B", 2), ("C", 5), ("D", 50)]
    curve, total = triage.cutoff_curve(unknown)
    assert total == 58
    calls = [c[1] for c in curve]
    assert calls == sorted(calls, reverse=True)
