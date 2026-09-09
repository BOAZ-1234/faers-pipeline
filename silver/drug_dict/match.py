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
from normalize import normalize_query, extract_bracket_alt, split_backslash_parts


def load_dictionary() -> tuple[set[str], set[str], set[str]]:
    """drug_ingredient_map에서 (브랜드명 집합, 성분명 집합, 등록된 조합 집합) 반환.
    브랜드/성분명 둘 중 하나라도 맞으면 1단계에서 잡힌 것으로 본다(신고자가 성분명을
    그대로 적는 경우가 있음).

    known_combos는 ingredient_set 칼럼에서 뽑은, 실제 소스가 등록해준 복합제 조합
    집합이다 — "\" 구분 복합제를 부품 각각의 존재 여부만으로 인정하면(성분이 다 사전에
    있다는 이유만으로) 등록된 적 없는 조합까지 통과시켜버린다(실측: 1,371개가 이런
    "추론만으로 히트"였음, 특히 SODIUM CHLORIDE 등 염접미사가 떨어져 나가는 수액류에서
    두드러짐). 그래서 조합 자체가 등록돼 있는지를 봐야 한다."""
    products, ingredients, known_combos = set(), set(), set()
    with open(HERE / "drug_ingredient_map.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            p = r["medicinalproduct"].strip().upper()
            i = r["ingredient_norm"].strip().upper()
            if p:
                products.add(p)
            if i:
                ingredients.add(i)
            for cand in r["ingredient_set"].split(" ; "):
                cand = cand.strip()
                if cand:
                    known_combos.add(cand)
    return products, ingredients, known_combos


def _hit(key: str, products: set[str], ingredients: set[str]) -> bool:
    return key in products or key in ingredients


def lookup(name: str, products: set[str], ingredients: set[str], known_combos: set[str]) -> bool:
    if _hit(normalize_query(name), products, ingredients):
        return True

    # "ALBUTEROL [SALBUTAMOL]" — 괄호 안에 원본이 이미 동의어를 적어준 경우
    alt = extract_bracket_alt(name)
    if alt and _hit(normalize_query(alt), products, ingredients):
        return True

    # "ACETAMINOPHEN\OXYCODONE HYDROCHLORIDE" — "\"로 구분된 복합제. 부품이 각각
    # 알려진 성분인지가 아니라, 이 조합 자체가 실제 소스에 등록된 적 있는지를 본다
    # (§5-1 ingredient_set과 동일한 exact-match 방식 — 추론이 아니라 조회)
    parts = split_backslash_parts(name)
    if parts:
        combo_key = "|".join(sorted(normalize_query(p).lower() for p in parts))
        if combo_key in known_combos:
            return True

    return False
