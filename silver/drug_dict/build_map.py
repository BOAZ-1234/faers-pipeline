"""
C세부1 — sources/output의 5개 CSV(openfda_label·openfda_ndc·rxnorm·mfds·diana)를
표기 규칙 하나로 통일해 병합 → drug_ingredient_map

ingredient_norm 정규화 규칙은 project-plan.html §5-1에 "대문자 · 공백 단일화 ·
염/수화물 접미사 제거"라고만 정의돼 있고, 접미사 목록 자체는 기획서에 없다.
아래 SALT_SUFFIXES는 일반적인 제약 명명 규칙 기준으로 채운 초안이라 검토 필요.

같은 (제품명,성분) 쌍인데 소스마다 UNII가 다른 경우가 있다(전량 데이터 기준 1,234쌍,
0.63% — 대부분 조합제에서 openFDA 원본의 active_ingredients/unii 배열 순서가
레코드 몇 개에서 어긋난 것으로 보임). "먼저 나온 값 사용"은 근거 없는 우연이라,
같은 키에 대해 등장한 모든 UNII를 세서 다수결로 채택한다.

ingredient_set(§5-1 표준, 정렬·소문자·파이프)은 복합제 성분을 한 소스 내에서 묶어
계산한다. 소스마다 같은 제품에 다른 성분 조합을 줄 수 있는데(캐스케이드 계약 회의
안건 B: 사전 충돌 시 후보 보존), 강제로 하나를 고르지 않고 " ; "로 구분해 후보를
모두 남긴다 — 아직 candidate/resolution 2테이블 스키마로 전환 전이라(안건 A 미확정)
잠정적으로 취한 방식.

DiAna(diana.csv)는 FAERS 원본 문자열을 직접 키로 쓰는 소스라 UNII가 없고, 대소문자도
소문자다. match.py에서 이 소스 유래 항목은 normalize_query() 없이 원본 그대로
대조해야 회수 효과가 난다(원본이 이미 FAERS 실제 표기라 재정규화하면 어긋남).
"""
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
SOURCES_DIR = HERE / "sources" / "output"
OUT_PATH = HERE / "drug_ingredient_map.csv"
FIELDNAMES = [
    "medicinalproduct", "ingredient_norm", "ingredient_set", "unii",
    "source", "method", "confidence", "dictionary_version",
]

# 사전 내용이 바뀔 때마다(소스 추가, 병합 로직 변경 등) 수동으로 올린다.
# 캐스케이드 쪽(Stage 3 corpus, Langfuse trace)이 이 값으로 재현성을 추적한다 — 자동 생성 금지.
DICTIONARY_VERSION = "d-2026Q3"

SOURCE_FILES = {
    "openfda_label": "openfda_label.csv",
    "openfda_ndc": "openfda_ndc.csv",
    "rxnorm": "rxnorm.csv",
    "mfds": "mfds.csv",
    "diana": "diana.csv",
}

# 초안 — 기획서에 목록이 없어 일반적인 제약 명명 규칙으로 채움. 검토 필요.
SALT_SUFFIXES = sorted([
    "MONOHYDRATE", "DIHYDRATE", "TRIHYDRATE", "ANHYDROUS", "HYDRATE",
    "HYDROCHLORIDE", "DIHYDROCHLORIDE", "HYDROBROMIDE", "HYDRIODIDE",
    "SULFATE", "DISULFATE", "PHOSPHATE", "DIPHOSPHATE",
    "SODIUM", "POTASSIUM", "CALCIUM", "MAGNESIUM",
    "MALEATE", "MESYLATE", "TARTRATE", "CITRATE", "ACETATE",
    "SUCCINATE", "FUMARATE", "BESYLATE", "BROMIDE", "CHLORIDE", "NITRATE",
], key=len, reverse=True)


def normalize_ingredient(raw: str) -> str:
    s = re.sub(r"\s+", " ", raw.strip()).upper()
    changed = True
    while changed:
        changed = False
        for suf in SALT_SUFFIXES:
            if s.endswith(" " + suf):
                s = s[: -(len(suf) + 1)].strip()
                changed = True
                break
    return s


def load_rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_ingredient_sets(rows_by_source: dict[str, list[dict]]) -> dict[str, dict[str, str]]:
    """소스별로 (제품명 대문자 → ingredient_set 문자열) 계산.
    같은 소스 안에서 같은 제품명에 딸린 성분을 전부 모아 정렬·소문자·파이프로 묶는다."""
    result = {}
    for source, rows in rows_by_source.items():
        product_ingrs = defaultdict(set)
        for r in rows:
            product = r["medicinalproduct"].strip()
            ingr = r["ingredient_norm"].strip()
            if not product or not ingr:
                continue
            product_ingrs[product.upper()].add(normalize_ingredient(ingr).lower())
        result[source] = {k: "|".join(sorted(v)) for k, v in product_ingrs.items()}
    return result


def main():
    products = {}  # key -> medicinalproduct(원본 표기, 첫 등장 값)
    unii_votes = {}  # key -> Counter(unii)
    sources_per_key = defaultdict(set)  # key -> {source, ...}
    per_source_count = {}
    rows_by_source = {}

    for source, fname in SOURCE_FILES.items():
        path = SOURCES_DIR / fname
        if not path.exists():
            print(f"⚠ {fname} 없음 — 건너뜀", flush=True)
            continue
        rows = load_rows(path)
        rows_by_source[source] = rows
        per_source_count[fname] = len(rows)
        for r in rows:
            product = r["medicinalproduct"].strip()
            if not product or not r["ingredient_norm"].strip():
                continue
            ingr_norm = normalize_ingredient(r["ingredient_norm"])
            key = (product.upper(), ingr_norm)
            products.setdefault(key, product)
            sources_per_key[key].add(source)
            if r["unii"]:
                unii_votes.setdefault(key, Counter())[r["unii"]] += 1

    ingredient_sets = build_ingredient_sets(rows_by_source)

    conflicts = sum(1 for c in unii_votes.values() if len(c) > 1)
    ingredient_set_conflicts = 0
    merged = {}
    for key, product in products.items():
        product_upper = key[0]
        votes = unii_votes.get(key)
        srcs = sorted(sources_per_key[key])

        # 이 (제품,성분) 쌍에 관여한 소스들이 그 제품에 대해 각자 낸 ingredient_set 후보
        candidates = []
        for s in srcs:
            v = ingredient_sets.get(s, {}).get(product_upper)
            if v and v not in candidates:
                candidates.append(v)
        if len(candidates) > 1:
            ingredient_set_conflicts += 1
        ingredient_set = " ; ".join(candidates)  # 후보 보존(안건 B) — 강제로 하나 고르지 않음

        merged[key] = {
            "medicinalproduct": product,
            "ingredient_norm": key[1],
            "ingredient_set": ingredient_set,
            "unii": votes.most_common(1)[0][0] if votes else "",
            "source": "|".join(srcs),
            "method": "사전",
            "confidence": "",
            "dictionary_version": DICTIONARY_VERSION,
        }

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(merged.values())

    print("소스별 원본 행 수:", flush=True)
    for fname, n in per_source_count.items():
        print(f"  {fname}: {n}", flush=True)
    unii_filled = sum(1 for v in merged.values() if v["unii"])
    print(f"\n병합 완료: 고유 (제품명, 성분) 쌍 {len(merged)}개 (UNII 채워짐 {unii_filled}개, "
          f"소스 간 UNII 충돌 {conflicts}개 — 다수결로 해소, "
          f"소스 간 ingredient_set 충돌(복합제 후보 2개 이상) {ingredient_set_conflicts}개 — 후보 보존)")
    print(f"→ {OUT_PATH.relative_to(HERE.parent.parent)}")


if __name__ == "__main__":
    main()
