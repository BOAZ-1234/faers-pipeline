"""Stage 3 평가 체계 보정 — 비성분/동의어 매핑.

DiAna 정답지에는 Stage 3가 처리할 수 없는 두 종류 라벨이 섞여 있다:

(A) OUT_OF_SCOPE 라벨 — Triage가 분리했어야 하는 것들:
   - "*, UNSPECIFIED"           : 성분 특정 못한 경우 (VITAMINS/MINERALS 등)
   - "SKIN CARE", "DEVICE", "PLACEBO, TRIAL", "NO MEDICATION", "UNSPECIFIED"
   - "COVID-19 VACCINE"         : 세부 백신명 아닌 상위 분류
   이것들은 Stage 3 평가에서 제외한다 (retrieval이 못 잡는 게 당연).

(B) 동의어 라벨 — 사전은 세부/화학명을 쓰는데 정답지는 공용명/상위명:
   - VITAMIN B1 ↔ thiamine, VITAMIN B2 ↔ riboflavin ...
   - PARACETAMOL ↔ acetaminophen (영미 표기 차)
   - ACETYLSALICYLIC ACID ↔ aspirin
   - INSULIN ↔ insulin {glargine, aspart, lispro, ...}
   이것들은 매칭 시 양쪽을 서로 정답으로 인정한다.
"""
from __future__ import annotations


# 평가에서 제외할 라벨 (Triage OUT_OF_SCOPE에 들어갔어야 하는 것들)
OUT_OF_SCOPE_LABELS = {
    "UNSPECIFIED",
    "SKIN CARE",
    "PLACEBO, TRIAL",
    "NO MEDICATION",
    "COVID-19 VACCINE",
}


def is_out_of_scope(ingredient: str) -> bool:
    """Stage 3가 처리할 수 없는 라벨인가.

    - 블랙리스트 완전 일치
    - ", UNSPECIFIED"로 끝나는 모든 라벨 (VITAMINS, UNSPECIFIED / MINERALS, UNSPECIFIED / etc.)
    """
    s = ingredient.strip().upper()
    if s in OUT_OF_SCOPE_LABELS:
        return True
    if s.endswith(", UNSPECIFIED"):
        return True
    return False


def filter_out_of_scope_gold(gold: dict[str, set[str]]) -> tuple[dict[str, set[str]], int]:
    """gold에서 OUT_OF_SCOPE 라벨만 가진 쿼리 제거.
    복합제처럼 일부만 OUT_OF_SCOPE인 경우는 유지하고 그 라벨만 뺀다.

    반환: 필터링된 gold, 제거된 쿼리 수"""
    out = {}
    removed = 0
    for q, ingrs in gold.items():
        kept = {i for i in ingrs if not is_out_of_scope(i)}
        if kept:
            out[q] = kept
        else:
            removed += 1
    return out, removed


# 동의어 — 라벨 → 사전 쪽 canonical 세트
# 양방향으로 쓴다: 정답지가 왼쪽을 쓰고 corpus top-K에 오른쪽이 들어오면 hit.
#   왼쪽은 DiAna 정답지 라벨 (대문자, 영국식/상위명)
#   오른쪽은 사전/corpus의 성분명 (소문자, 화학명/세부종)
SYNONYMS: dict[str, set[str]] = {
    # 비타민 — 공용명 vs 화학명
    "VITAMIN A":   {"vitamin a", "retinol", "retinyl palmitate", "retinyl acetate", "vitamin a palmitate", "beta carotene"},
    "VITAMIN B1":  {"thiamine", "thiamine mononitrate", "thiamine hydrochloride"},
    "VITAMIN B2":  {"riboflavin", "riboflavin 5'-phosphate"},
    "VITAMIN B3":  {"niacin", "niacinamide", "nicotinamide"},
    "VITAMIN B5":  {"pantothenic acid", "dexpanthenol", "calcium pantothenate"},
    "VITAMIN B6":  {"pyridoxine", "pyridoxine hydrochloride", "pyridoxal 5-phosphate"},
    "VITAMIN B7":  {"biotin"},
    "VITAMIN B9":  {"folic acid", "folate", "levomefolate", "levomefolate calcium"},
    "VITAMIN B12": {"cyanocobalamin", "methylcobalamin", "hydroxocobalamin"},
    "VITAMIN C":   {"ascorbic acid", "sodium ascorbate"},
    "VITAMIN D":   {"cholecalciferol", "ergocalciferol", "vitamin d2", "vitamin d3"},
    "VITAMIN D3":  {"cholecalciferol"},
    "VITAMIN D2":  {"ergocalciferol"},
    "VITAMIN E":   {".alpha.-tocopherol", ".alpha.-tocopherol acetate, dl-", "tocopherol", "tocopheryl acetate"},
    "VITAMIN K":   {"phytonadione", "phylloquinone", "menaquinone", "vitamin k1"},

    # 영미 표기 차
    "PARACETAMOL":         {"acetaminophen"},
    "ACETYLSALICYLIC ACID": {"aspirin"},
    "ADRENALINE":          {"epinephrine"},
    "NORADRENALINE":       {"norepinephrine"},
    "SALBUTAMOL":          {"albuterol"},

    # 상위 성분 → 세부종 묶음
    "INSULIN": {
        "insulin", "insulin aspart", "insulin aspart protamine",
        "insulin glargine", "insulin glulisine", "insulin lispro",
        "insulin lispro protamine", "insulin detemir", "insulin degludec",
        "insulin human", "insulin isophane", "insulin regular",
    },

    # 미네랄 — 원소명 vs 염
    "CALCIUM":  {"calcium", "calcium carbonate", "calcium citrate", "calcium gluconate",
                 "calcium phosphate", "calcium chloride", "calcium acetate", "calcium lactate"},
    "ZINC":     {"zinc", "zinc oxide", "zinc gluconate", "zinc sulfate", "zinc chloride",
                 "zinc acetate", "zinc citrate"},
    "IRON":     {"iron", "ferrous", "ferrous sulfate", "ferrous fumarate", "ferrous gluconate",
                 "ferric", "iron sucrose", "ferric carboxymaltose"},
    "MAGNESIUM": {"magnesium", "magnesium oxide", "magnesium citrate", "magnesium sulfate",
                  "magnesium chloride", "magnesium hydroxide"},
    "SELENIUM":  {"selenium"},
    "IODINE":    {"iodine", "potassium iodide"},
    "MANGANESE": {"manganese", "manganese sulfate", "manganese chloride"},
    "CHROMIUM":  {"chromium", "chromium picolinate", "chromium nicotinate", "chromium chloride"},
    "CUPRIC OXIDE": {"cupric oxide", "copper", "copper sulfate", "cupric sulfate"},
    "POTASSIUM": {"potassium", "potassium chloride", "potassium bicarbonate", "potassium citrate"},
    "SODIUM":    {"sodium", "sodium chloride", "sodium bicarbonate"},
}

# 역방향 인덱스 — 사전 쪽 이름 → DiAna 라벨
_REVERSE: dict[str, set[str]] = {}
for label, variants in SYNONYMS.items():
    for v in variants:
        _REVERSE.setdefault(v.lower(), set()).add(label)


def expand_candidate_names(ingrs: set[str]) -> set[str]:
    """corpus top-K에서 뽑은 성분 이름 집합을 동의어로 확장.
    대문자 성분명으로 반환 (gold와 비교 가능하게)."""
    expanded = set(ingrs)
    for name in ingrs:
        key = name.strip().lower()
        for label in _REVERSE.get(key, ()):
            expanded.add(label.upper())
    return expanded


def expand_gold_labels(ingrs: set[str]) -> set[str]:
    """정답 라벨 집합을 동의어로 확장 (DiAna 라벨 → 사전 쪽 이름들도 정답으로 인정).
    대문자 통일."""
    expanded = set(ingrs)
    for name in ingrs:
        key = name.strip().upper()
        for v in SYNONYMS.get(key, ()):
            expanded.add(v.upper())
    return expanded
