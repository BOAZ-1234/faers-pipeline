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
])
def test_protected(name):
    assert triage.out_of_scope_reason(name) is None


def test_cutoff_curve_monotonic():
    """컷오프가 커지면 LLM 호출 수·커버 신고는 단조 감소해야 한다."""
    unknown = [("A", 1), ("B", 2), ("C", 5), ("D", 50)]
    curve, total = triage.cutoff_curve(unknown)
    assert total == 58
    calls = [c[1] for c in curve]
    assert calls == sorted(calls, reverse=True)
