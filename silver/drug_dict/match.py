"""
C세부1 — 1단계 정규화 + 사전 조회 (§4-2)
FAERS 자유기재 약물명(예: "DURAGESIC-100")에서 용량/제형을 뗀 뒤
drug_ingredient_map에서 브랜드명 또는 성분명으로 조회한다.

용량/제형 단어 목록은 §4-2에 "DURAGESIC-100 → 용량/제형 떼기 → DURAGESIC"라는
예시 하나만 있고 정확한 목록은 기획서에 없어 실제 FAERS drugname 표본을 보고
직접 채운 초안이다 — 검토 필요.
"""
import csv
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from build_map import SALT_SUFFIXES  # 사전 만들 때 쓴 염/수화물 접미사와 동일 목록

# 제형·투여경로·방출형태·약전 표기 등 — 끝에서부터 반복적으로 뗀다
DOSAGE_FORM_WORDS = {
    "TABLET", "TABLETS", "TAB", "TABS", "CAPSULE", "CAPSULES", "CAP", "CAPS",
    "INJECTION", "INJECTABLE", "SOLUTION", "SOLN", "CREAM", "PATCH", "GEL",
    "SYRUP", "SUSPENSION", "SPRAY", "OINTMENT", "LOTION", "POWDER",
    "DROPS", "LOZENGE", "SUPPOSITORY", "ORAL", "TOPICAL", "IV", "IM", "SC",
    "ER", "XR", "SR", "CR", "DR", "EC", "IR", "HFA", "USP", "NOS", "PF",
    "EXTENDED", "RELEASE", "DELAYED", "IMMEDIATE",
    "POUDRE", "POUR",  # 프랑스어 라벨(POUDRE POUR SOLUTION INJECTABLE)이 섞여 들어옴
    # 단위(공백으로 떨어진 경우: "500 MG")
    "MG", "MCG", "G", "KG", "ML", "L", "MEQ", "IU", "UNIT", "UNITS", "%",
}
_UNIT_ATTACHED = re.compile(r"^\d+(\.\d+)?(MG|MCG|G|KG|ML|L|MEQ|IU|UNIT|UNITS|%)$", re.I)
_PURE_NUM = re.compile(r"^\d+(\.\d+)?$")
_TRAILING_HYPHEN_NUM = re.compile(r"^(.*\S)-\d+(\.\d+)?$")


def normalize_query(name: str) -> str:
    """자유기재 약물명에서 용량/제형을 반복적으로 떼어 핵심 이름만 남긴다.
    "DURAGESIC-100" → "DURAGESIC", "LISINOPRIL TABLETS USP, 20MG" → "LISINOPRIL"."""
    s = name.strip().upper().rstrip(".").strip()
    changed = True
    while changed:
        changed = False
        s = s.rstrip(", ").strip()
        m = _TRAILING_HYPHEN_NUM.match(s)
        if m:
            s = m.group(1)
            changed = True
            continue
        tokens = s.split()
        if not tokens:
            break
        last = tokens[-1].rstrip(",")
        if last in DOSAGE_FORM_WORDS or _UNIT_ATTACHED.match(last) or _PURE_NUM.match(last):
            s = " ".join(tokens[:-1])
            changed = True
            continue
        # 사전 만들 때와 같은 염/수화물 접미사 (예: "AZITHROMYCIN ANHYDROUS" → "AZITHROMYCIN")
        if len(tokens) > 1 and last in SALT_SUFFIXES:
            s = " ".join(tokens[:-1])
            changed = True
    return s.strip()


def load_dictionary() -> tuple[set[str], set[str]]:
    """drug_ingredient_map에서 (브랜드명 집합, 성분명 집합) 반환 — 둘 중 하나라도
    맞으면 1단계에서 잡힌 것으로 본다(신고자가 성분명을 그대로 적는 경우가 있음)."""
    products, ingredients = set(), set()
    with open(HERE / "drug_ingredient_map.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            p = r["medicinalproduct"].strip().upper()
            i = r["ingredient_norm"].strip().upper()
            if p:
                products.add(p)
            if i:
                ingredients.add(i)
    return products, ingredients


def lookup(name: str, products: set[str], ingredients: set[str]) -> bool:
    key = normalize_query(name)
    return key in products or key in ingredients
