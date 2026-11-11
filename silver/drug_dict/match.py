"""
C세부1 — 1단계 정규화 + 사전 조회 (§4-2)
FAERS 자유기재 약물명(예: "DURAGESIC-100")에서 용량/제형을 뗀 뒤
drug_ingredient_map에서 브랜드명 또는 성분명으로 조회한다.

정규화 자체(normalize_query)는 normalize.py로 뽑아냄 — 캐스케이드 계약 회의
안건 D(2026-09-08)에서 전 단계 공통 함수로 확정됐다.
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from normalize import normalize_query, extract_bracket_alt, split_backslash_parts, resolve_prod_ai


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


def lookup_with_prod_ai(name: str, prod_ai: str | None, products: set[str],
                         ingredients: set[str], known_combos: set[str]) -> bool:
    """1단계: 사전 조회(lookup) 먼저 시도, 실패하면 prod_ai 폴백.
    prod_ai는 FDA가 직접 채운 필드라 사전 대조 없이 정규화만 해서 받아들인다
    (캐스케이드 계약 회의 후속 논의, §7 게이트 기준을 DiAna 정답지 기반으로 바꾸면서
    prod_ai는 입력으로 재분류함 — 2026-09-18)."""
    if lookup(name, products, ingredients, known_combos):
        return True
    return resolve_prod_ai(prod_ai) is not None


class DictionaryIndex:
    """브랜드명 키와 성분명 키는 "매치됐을 때 뭘 확정 성분으로 볼지"가 다르다 —
    _hit()(lookup()이 쓰는 판정)는 둘을 하나의 bool로 뭉뚱그리지만, 여기서는
    실제 ingredient_set까지 되짚어야 해서 분리해야 한다:

    - medicinalproduct 키 매치 = "이 행이 바로 그 제품" → 그 행의 ingredient_set(복합제
      전체)을 그대로 쓴다.
    - ingredient_norm 키 매치 = "신고자가 성분명 자체를 적었다" → 확정할 성분은 그
      성분 하나뿐이다. 그런데 ingredient_norm="ASPIRIN"은 아스피린을 포함한 수십 개
      복합제 행에도 전부 달려 있어서(실측: ASPIRIN 하나로 41개 서로 다른
      ingredient_set과 엮임), 그 행들의 ingredient_set을 그대로 후보로 주면 "아스피린
      하나 적었는데 복합제 41개 중 아무거나"가 되는 오매핑이 난다. 그래서 성분명
      매치는 **그 성분 하나**로 고정하고, UNII만 "그 성분 하나뿐인 행"(단일성분 제품)
      에서 찾아 붙인다 — 그런 행이 아예 없으면 UNII는 빈값으로 둔다(흔치 않음).
    """

    def __init__(self):
        self.by_product: dict[str, list[dict]] = defaultdict(list)
        self.by_ingredient_unii: dict[str, set[str]] = defaultdict(set)
        self.known_ingredients: set[str] = set()

    @classmethod
    def load(cls) -> "DictionaryIndex":
        idx = cls()
        with open(HERE / "drug_ingredient_map.csv", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                ingredient_set = r["ingredient_set"].strip()
                unii = r["unii"].strip()
                source = r["source"].strip()
                p = r["medicinalproduct"].strip().upper()
                i = r["ingredient_norm"].strip().upper()
                if p:
                    idx.by_product[p].append(
                        {"ingredient_set": ingredient_set, "unii": unii, "source": source})
                if i:
                    idx.known_ingredients.add(i)
                    # 단일성분 제품 행(이 제품 = 이 성분 하나)에서만 UNII를 신뢰한다.
                    single = ingredient_set.split(" ; ")[0].strip() if ingredient_set else ""
                    if "|" not in single and single.lower() == i.lower() and unii:
                        idx.by_ingredient_unii[i].add(unii)
        return idx

    def candidates_for_product(self, key: str) -> list[dict] | None:
        rows = self.by_product.get(key)
        if not rows:
            return None
        seen: dict[str, dict] = {}
        for r in rows:
            # ingredient_set 안에 " ; "로 소스 간 충돌 후보가 이미 같이 들어있는 경우가
            # 있다(build_map.py 안건 B: "강제로 하나를 고르지 않고 후보를 모두 남긴다") —
            # 여기서도 같은 원칙으로 쪼개서 후보 각각을 살린다.
            for cand in r["ingredient_set"].split(" ; "):
                cand = cand.strip()
                if cand and cand not in seen:
                    seen[cand] = {"ingredient_set": cand, "unii": r["unii"], "source": r["source"]}
        return list(seen.values()) or None

    def candidate_for_ingredient(self, key: str) -> list[dict] | None:
        if key not in self.known_ingredients:
            return None
        uniis = self.by_ingredient_unii.get(key)
        unii = next(iter(uniis)) if uniis and len(uniis) == 1 else ""
        return [{"ingredient_set": key.lower(), "unii": unii, "source": "ingredient_norm"}]


def load_dictionary_index() -> DictionaryIndex:
    return DictionaryIndex.load()


def lookup_full(name: str, index: "DictionaryIndex", known_combos: set[str],
                 prod_ai: str | None = None) -> dict | None:
    """lookup()/lookup_with_prod_ai()와 같은 판정 경로(직접 조회 → 괄호 동의어 →
    "\\" 복합제 조합 → prod_ai 폴백)를 그대로 따라가되, bool 대신 매칭 근거를
    돌려준다 — build_resolution.py가 이걸로 stg_resolution/stg_candidate 행을
    만든다. candidates가 1개면 확정, 여러 개면(사전 충돌) 보류로 쓰면 된다.
    브랜드명 키로 먼저 보고(더 구체적인 매치), 안 되면 성분명 키로 본다 —
    둘 다 걸리는 이름(브랜드명이 곧 성분명인 단일제)은 결과가 같아 순서가
    상관없지만, 성분명이 다른 제품의 ingredient_norm으로도 등재돼 있는 경우
    (예: "ASPIRIN"이 자기 자신의 브랜드명이면서 동시에 수십 개 복합제의 성분이기도
    함) 브랜드명 매치를 우선해야 그 행 자신의 ingredient_set을 올바르게 쓴다."""
    key = normalize_query(name)
    cands = index.candidates_for_product(key) or index.candidate_for_ingredient(key)
    if cands:
        return {"matched_key": key, "via": "dictionary", "candidates": cands}

    alt = extract_bracket_alt(name)
    if alt:
        alt_key = normalize_query(alt)
        cands = index.candidates_for_product(alt_key) or index.candidate_for_ingredient(alt_key)
        if cands:
            return {"matched_key": alt_key, "via": "bracket_alt", "candidates": cands}

    parts = split_backslash_parts(name)
    if parts:
        combo_key = "|".join(sorted(normalize_query(p).lower() for p in parts))
        if combo_key in known_combos:
            return {
                "matched_key": combo_key, "via": "combo",
                "candidates": [{"ingredient_set": combo_key, "unii": "", "source": ""}],
            }

    if prod_ai:
        resolved = resolve_prod_ai(prod_ai)
        if resolved:
            return {
                "matched_key": resolved, "via": "prod_ai",
                "candidates": [{"ingredient_set": resolved, "unii": "", "source": "prod_ai"}],
            }

    return None
