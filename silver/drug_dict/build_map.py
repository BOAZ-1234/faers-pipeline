"""
C세부1 — sources/output의 4개 CSV(openfda_label·openfda_ndc·rxnorm·mfds)를
표기 규칙 하나로 통일해 병합 → drug_ingredient_map 1차본

ingredient_norm 정규화 규칙은 project-plan.html §5-1에 "대문자 · 공백 단일화 ·
염/수화물 접미사 제거"라고만 정의돼 있고, 접미사 목록 자체는 기획서에 없다.
아래 SALT_SUFFIXES는 일반적인 제약 명명 규칙 기준으로 채운 초안이라 검토 필요.

같은 (제품명,성분) 쌍인데 소스마다 UNII가 다른 경우가 있다(전량 데이터 기준 1,234쌍,
0.63% — 대부분 조합제에서 openFDA 원본의 active_ingredients/unii 배열 순서가
레코드 몇 개에서 어긋난 것으로 보임). "먼저 나온 값 사용"은 근거 없는 우연이라,
같은 키에 대해 등장한 모든 UNII를 세서 다수결로 채택한다.
"""
import csv
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
SOURCES_DIR = HERE / "sources" / "output"
OUT_PATH = HERE / "drug_ingredient_map.csv"
FIELDNAMES = ["medicinalproduct", "ingredient_norm", "unii", "method", "confidence"]

SOURCE_FILES = ["openfda_label.csv", "openfda_ndc.csv", "rxnorm.csv", "mfds.csv"]

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


def main():
    products = {}  # key -> medicinalproduct(원본 표기, 첫 등장 값)
    unii_votes = {}  # key -> Counter(unii)
    per_source_count = {}

    for fname in SOURCE_FILES:
        path = SOURCES_DIR / fname
        if not path.exists():
            print(f"⚠ {fname} 없음 — 건너뜀", flush=True)
            continue
        rows = load_rows(path)
        per_source_count[fname] = len(rows)
        for r in rows:
            product = r["medicinalproduct"].strip()
            if not product or not r["ingredient_norm"].strip():
                continue
            ingr_norm = normalize_ingredient(r["ingredient_norm"])
            key = (product.upper(), ingr_norm)
            products.setdefault(key, product)
            if r["unii"]:
                unii_votes.setdefault(key, Counter())[r["unii"]] += 1

    conflicts = sum(1 for c in unii_votes.values() if len(c) > 1)
    merged = {}
    for key, product in products.items():
        votes = unii_votes.get(key)
        merged[key] = {
            "medicinalproduct": product,
            "ingredient_norm": key[1],
            "unii": votes.most_common(1)[0][0] if votes else "",
            "method": "사전",
            "confidence": "",
        }

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(merged.values())

    print("소스별 원본 행 수:", flush=True)
    for fname, n in per_source_count.items():
        print(f"  {fname}: {n}", flush=True)
    unii_filled = sum(1 for v in merged.values() if v["unii"])
    print(f"\n병합 완료: 고유 (제품명, 성분) 쌍 {len(merged)}개 (UNII 채워짐 {unii_filled}개, 소스 간 값 충돌 {conflicts}개 — 다수결로 해소)")
    print(f"→ {OUT_PATH.relative_to(HERE.parent.parent)}")


if __name__ == "__main__":
    main()
