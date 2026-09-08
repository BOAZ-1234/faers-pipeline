"""
C세부1 — 1단계 정규화 + 사전 조회 (§4-2)
FAERS 자유기재 약물명(예: "DURAGESIC-100")에서 용량/제형을 뗀 뒤
drug_ingredient_map에서 브랜드명 또는 성분명으로 조회한다.

정규화 자체(normalize_query)는 normalize.py로 뽑아냄 — 캐스케이드 계약 회의
안건 D(2026-09-08)에서 전 단계 공통 함수로 확정됐다.
"""
import csv
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from normalize import normalize_query


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
