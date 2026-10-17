"""
C단계 3단계 전처리 — 잔여 triage + 규모 산정 (게이팅 역할, 액션플랜 §5 Task 1)

2단계(similarity.py)까지 통과하고 남은 잔여(`out/unmatched_for_stage3.csv`,
`name,n_reports`)를 embedding+LLM(3단계)에 태우기 **전에** 규칙으로 세 버킷으로 가른다.
비싼 LLM 호출은 진짜 미지(UNKNOWN)에만 쓰고, 나머지는 공짜 규칙으로 쳐낸다는 게
3단계 설계의 핵심(stage3-plan §2-2). 이 스크립트의 산출(특히 UNKNOWN 개수 Z)이
3-A Recall 측정·GPU 산정·Langfuse 요금의 분모가 된다.

버킷 (위에서부터 먼저 걸리는 순):
  OUT_OF_SCOPE : class/미상/NOS/비약물 — 붙일 성분 자체가 없음. LLM 보낼 가치 없음.
                 근거 = normalize.py PROD_AI_EXCLUDE·_NOS_PATTERN + similarity.py class_or_form_only.
                 (지금까지 이 가드들은 prod_ai/2단계 안에서만 돌아 drugname 잔여엔 그대로 남아 있었음.)
  BRAND_DOSE   : 브랜드+용량/제형. 재정규화→1단계 사전 재조회가 되면 LLM 없이 해결 가능.
                 사전(drug_ingredient_map.csv)이 로컬에 있어야 측정됨(gitignore).
  UNKNOWN      : 진짜 미지 브랜드·복합제·orphan — 이것만 3단계(embedding+LLM) 대상.

실행:
  python3 triage.py out/unmatched_for_stage3.csv
  python3 triage.py out/unmatched_for_stage3.csv --out-unknown-csv out/stage3_unknown.csv \
          --out-curve-csv out/stage3_cutoff_curve.csv
  python3 triage.py out/unmatched_for_stage3.csv --map silver/drug_dict/drug_ingredient_map.csv

사전 경로(--map)를 주거나 기본 경로에 drug_ingredient_map.csv가 있으면 BRAND_DOSE를
측정하고, 없으면 BRAND_DOSE는 비활성화(UNKNOWN에 합산)하고 경고만 낸다.
"""
import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from normalize import normalize_query, PROD_AI_EXCLUDE, _NOS_PATTERN
from similarity import class_or_form_only

# 빈도 컷오프 곡선에 찍을 "신고 ≥N건" 지점들. "신고 몇 건 이상만 LLM에 태우나"의 후보.
CUTOFFS = [1, 2, 3, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000]


def load_rows(path: Path) -> list[tuple[str, int]]:
    """입력 CSV(name,n_reports)를 읽는다. n_reports 없으면 0으로."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            try:
                n = int(r.get("n_reports") or 0)
            except ValueError:
                n = 0
            rows.append((name, n))
    return rows


# --- 추가 OUT_OF_SCOPE 규칙 (잔여 감사로 검증, 2026-10-05 / #48 리뷰 2026-10-17) -----
# 비타민: "어떤 비타민인지 특정 안 되는 것"만 제외한다. 특정 비타민(VITAMIN D/C/B12,
#   대괄호에 성분이 적힌 것, 특정 화학/미네랄명)은 매핑 가능한 진짜 성분이라 보호.
#   감사 실측: 비특정만 추리면 9,078개(신고 115,537), 특정분 7,240개는 보호해야 손실 0.
_VIT_CLASS = re.compile(r"\b(MULTI\s?VITAMIN|MULTIVITAMINS?|VITAMIN[S]?|MINERAL[S]?)\b")
# 번호가 붙은 특정 비타민: VITAMIN B12/B-12/B 12/B 2/D3 — 글자와 숫자 사이 구분자(공백·
#   하이픈)를 허용해야 한다. normalize_query가 'VITAMIN B-12'의 숫자를 떼어 'VITAMIN B'
#   (PROD_AI_EXCLUDE)로 떨어뜨리므로, raw 기준으로 번호를 잡아 보호한다(#48 리뷰: 185개·
#   신고 2,773건이 이렇게 손실되고 있었음).
_VIT_LETTER_NUM = re.compile(r"\bVITAMIN\s*[A-K][\s-]*\d+")
# 단독으로도 특정인 비타민 문자: A·C·D·E·K (B는 비특정 B군이라 제외).
_VIT_SPECIFIC_LETTER = re.compile(r"\bVITAMIN\s*[ACDEK]\b")
# 특정 화학/미네랄명 — 그 자체로 매핑 가능한 진짜 성분.
_VIT_SPECIFIC_CHEM = re.compile(
    r"\b(CALCIUM|IRON|ZINC|MAGNESIUM|POTASSIUM|SODIUM|SELENIUM|FOLIC|FOLATE|"
    r"ASCORBIC|BIOTIN|NIACIN|THIAMINE?|RIBOFLAVIN|PYRIDOXINE|COBALAMIN|TOCOPHEROL|"
    r"RETINOL|CHOLECALCIFEROL|COLECALCIFEROL|OMEGA|FISH\s?OIL|D3|B12|B6)\b")
# 명백 화장품(약용 가능성 없는 것만). SHAMPOO/SOAP/SUNSCREEN/MOUTHWASH 등은 약용 제품이
#   섞일 수 있어(예: MIRACLE MOUTHWASH=조제약) 일부러 제외 → UNKNOWN에 남겨 LLM 판정.
#   "SERUM"도 의학 혈청(IMMUNE SERUM GLOBULIN·항혈청)과 겹쳐 제외 — 화장품 세럼은
#   대개 EYE BRIGHTENING 등 다른 키워드로 잡힌다.
_COSMETIC_CLEAR = re.compile(
    r"\b(FACE\s?WASH|CLEANSING|CLEANSER|TONER|MOISTURI[SZ]ER|MASCARA|"
    r"LIPSTICK|MAKEUP|EYE\s?BRIGHTENING|BLACKHEAD|PORE)\b")
# 화장품 키워드가 있어도 OTC 활성성분이 적혀 있으면 진짜 약이므로 제외 금지(#48 리뷰:
#   "...WITH BENZOYL PEROXIDE 1%"·SPF 표기 제품 등 133개·신고 215건이 CLEANSER 등
#   화장품 키워드에 걸려 손실되고 있었음).
_COSMETIC_ACTIVE = re.compile(
    r"\b(BENZOYL\s?PEROXIDE|SALICYLIC\s?ACID|ADAPALENE|SULFUR|SPF|SUNSCREEN|"
    r"HYDROCORTISONE|CLOTRIMAZOLE|KETOCONAZOLE|MICONAZOLE|RETINOL|GLYCOLIC\s?ACID)\b")
# 성분 나열 구분자: 대괄호 · 세미콜론 · 역슬래시.
_PART_SEP = re.compile(r"[\[\];\\]")


def is_specific_vitamin(name_up: str) -> bool:
    """특정 비타민/미네랄/성분이 지정됐는지 — 지정됐으면 어떤 제외 규칙보다 먼저 보호한다."""
    return bool("[" in name_up
                or _VIT_LETTER_NUM.search(name_up)
                or _VIT_SPECIFIC_LETTER.search(name_up)
                or _VIT_SPECIFIC_CHEM.search(name_up))


def is_nonspecific_vitamin(name_up: str) -> bool:
    if not _VIT_CLASS.search(name_up):
        return False
    return not is_specific_vitamin(name_up)


def _has_specific_besides_nos(name_up: str) -> bool:
    """이름이 성분 나열(대괄호/';'/'\\')이고 NOS가 아닌 '특정' 부품이 하나라도 있으면 True.
    그러면 'NOS가 들어있다'는 이유로 전체를 버리지 않고 UNKNOWN에 남겨 LLM/사전이 특정
    성분을 살린다(#48 리뷰: ESTROGEN NOS;METHYLTESTOSTERONE 등 1,131개·신고 5,315건)."""
    if not _PART_SEP.search(name_up):         # 나열 구조가 아니면 단일 'X NOS' → 통상 제외
        return False
    for p in (p.strip() for p in _PART_SEP.split(name_up)):
        if not p or _NOS_PATTERN.search(p) or class_or_form_only(p) or p in PROD_AI_EXCLUDE:
            continue
        return True
    return False


def out_of_scope_reason(name: str) -> str | None:
    """class/미상/NOS/비약물이면 사유 문자열, 아니면 None.
    NOS는 raw 기준으로 본다(normalize_query가 끝의 NOS를 떼어 'INSULIN NOS'→'INSULIN'이
    되면 못 잡으므로). PROD_AI_EXCLUDE는 raw·정규화 양쪽으로 본다."""
    up = name.strip().upper()
    # 특정 비타민/성분은 어떤 제외 규칙보다 먼저 보호한다. 안 그러면 normalize_query가
    # 숫자를 떼어 'VITAMIN B-12'→'VITAMIN B'(PROD_AI_EXCLUDE)로 떨어진다(#48 리뷰).
    if is_specific_vitamin(up):
        return None
    if class_or_form_only(name):              # 전부 치료군/제형/수식어 토큰
        return "class/form"
    # "... NOS"는 제외하되, 성분 나열 중 NOS 아닌 특정 부품이 섞였으면 전체를 버리지
    # 않고 UNKNOWN 유지(#48 리뷰 — ESTROGEN NOS;METHYLTESTOSTERONE 등).
    if _NOS_PATTERN.search(up) and not _has_specific_besides_nos(up):
        return "NOS"
    if up in PROD_AI_EXCLUDE or normalize_query(name) in PROD_AI_EXCLUDE:
        return "prod_ai_exclude"              # VITAMINS, UNSPECIFIED INGREDIENT, COSMETICS ...
    if is_nonspecific_vitamin(up):            # MULTIVITAMINS, MINERALS\VITAMINS (특정분 보호)
        return "vitamin_class"
    # 화장품 키워드가 있어도 OTC 활성성분(벤조일퍼옥사이드·살리실산·SPF…)이 적혀 있으면
    # 진짜 약 → 제외 금지(#48 리뷰).
    if _COSMETIC_CLEAR.search(up) and not _COSMETIC_ACTIVE.search(up):
        return "cosmetic"                     # PROACTIV FACE WASH/TONER/MOISTURIZER ...
    return None


def is_out_of_scope(name: str) -> bool:
    return out_of_scope_reason(name) is not None


def load_dict_lookup(map_path: Path):
    """주어진 사전 경로에서 (브랜드·성분·조합) 집합을 만들어 lookup 함수(name->bool)를 반환.
    match.load_dictionary는 경로가 silver/drug_dict/로 하드코딩돼 있어 재사용 못 하므로,
    같은 로딩 로직을 여기서 임의 경로로 수행한다. 매칭 로직(match.lookup)은 그대로 재사용."""
    from match import lookup  # 매칭 로직은 공유
    products, ingredients, known_combos = set(), set(), set()
    with open(map_path, newline="", encoding="utf-8") as f:
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
    print(f"  사전: 브랜드 {len(products):,} / 성분 {len(ingredients):,} / 조합 {len(known_combos):,}")
    return lambda name: lookup(name, products, ingredients, known_combos)


def triage(rows, dict_lookup):
    """각 (name, n)을 OUT_OF_SCOPE / BRAND_DOSE / UNKNOWN 으로 분류.
    dict_lookup이 None이면 BRAND_DOSE 판정을 건너뛰고 UNKNOWN으로 보낸다.
    OUT_OF_SCOPE는 사유별 집계(reasons)도 함께 반환한다(감사용)."""
    buckets = {"OUT_OF_SCOPE": [], "BRAND_DOSE": [], "UNKNOWN": []}
    reasons = defaultdict(lambda: [0, 0])  # reason -> [고유명 수, 신고합]
    for name, n in rows:
        reason = out_of_scope_reason(name)
        if reason is not None:
            buckets["OUT_OF_SCOPE"].append((name, n))
            reasons[reason][0] += 1
            reasons[reason][1] += n
        elif dict_lookup is not None and dict_lookup(name):
            buckets["BRAND_DOSE"].append((name, n))
        else:
            buckets["UNKNOWN"].append((name, n))
    return buckets, dict(reasons)


def summarize_buckets(buckets, total_names, total_reports):
    print("\n=== triage 버킷 ===")
    print(f"{'버킷':<14}{'고유명':>12}{'(%)':>8}{'신고합':>14}{'(%)':>8}")
    for key in ("OUT_OF_SCOPE", "BRAND_DOSE", "UNKNOWN"):
        items = buckets[key]
        n_names = len(items)
        n_reports = sum(n for _, n in items)
        pn = n_names / total_names * 100 if total_names else 0
        pr = n_reports / total_reports * 100 if total_reports else 0
        print(f"{key:<14}{n_names:>12,}{pn:>7.2f}%{n_reports:>14,}{pr:>7.2f}%")
    print(f"{'합계':<14}{total_names:>12,}{100.0:>7.2f}%{total_reports:>14,}{100.0:>7.2f}%")


def cutoff_curve(unknown):
    """UNKNOWN 버킷에서 '신고 ≥N건'인 고유명 개수·신고합·커버리지를 N별로.
    'LLM 호출 수'(=고유명 개수)와 '잔여 신고 커버리지'의 trade-off 곡선."""
    unk_total_reports = sum(n for _, n in unknown)
    curve = []
    for cutoff in CUTOFFS:
        kept = [(name, n) for name, n in unknown if n >= cutoff]
        n_calls = len(kept)                       # = LLM 호출 수
        rep = sum(n for _, n in kept)
        cov = rep / unk_total_reports * 100 if unk_total_reports else 0
        curve.append((cutoff, n_calls, rep, cov))
    return curve, unk_total_reports


def summarize_curve(curve, unk_total_reports):
    print("\n=== UNKNOWN 빈도 컷오프 곡선 (신고 ≥N건만 LLM) ===")
    print(f"UNKNOWN 신고합 = {unk_total_reports:,}")
    print(f"{'컷오프 ≥N':>10}{'LLM 호출 수':>14}{'커버 신고합':>16}{'UNKNOWN 커버%':>16}")
    for cutoff, n_calls, rep, cov in curve:
        print(f"{cutoff:>10,}{n_calls:>14,}{rep:>16,}{cov:>15.2f}%")


def write_rows_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "n_reports"])
        for name, n in sorted(rows, key=lambda x: -x[1]):
            w.writerow([name, n])


def write_curve_csv(path: Path, curve):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cutoff_n_reports", "llm_calls", "reports_covered", "unknown_coverage_pct"])
        for cutoff, n_calls, rep, cov in curve:
            w.writerow([cutoff, n_calls, rep, f"{cov:.4f}"])


def main():
    ap = argparse.ArgumentParser(description="3단계 전 잔여 triage + 규모 산정")
    ap.add_argument("input", type=Path, help="잔여 CSV (name,n_reports) — out/unmatched_for_stage3.csv")
    ap.add_argument("--map", type=Path, default=HERE.parent.parent / "out" / "drug_ingredient_map.csv",
                    help="사전 CSV 경로 (BRAND_DOSE 재조회용, 없으면 BRAND_DOSE 비활성화)")
    ap.add_argument("--out-unknown-csv", type=Path, help="UNKNOWN 버킷(진짜 LLM 대상)만 저장")
    ap.add_argument("--out-oos-csv", type=Path, help="OUT_OF_SCOPE 버킷 저장(감사용)")
    ap.add_argument("--out-curve-csv", type=Path, help="UNKNOWN 빈도 컷오프 곡선 저장")
    args = ap.parse_args()

    rows = load_rows(args.input)
    total_names = len(rows)
    total_reports = sum(n for _, n in rows)
    print(f"입력: {total_names:,} 고유명 / 신고합 {total_reports:,}")

    if args.map.exists():
        print(f"사전 로드: {args.map}")
        dict_lookup = load_dict_lookup(args.map)
    else:
        print(f"[주의] 사전 없음({args.map}) → BRAND_DOSE 비활성화, UNKNOWN에 합산됨.")
        print("    BRAND_DOSE 실수치는 사전이 있는 환경에서 --map 으로 다시 실행.")
        dict_lookup = None

    buckets, reasons = triage(rows, dict_lookup)
    summarize_buckets(buckets, total_names, total_reports)

    print("\n=== OUT_OF_SCOPE 사유별 ===")
    for reason, (nn, nr) in sorted(reasons.items(), key=lambda x: -x[1][1]):
        print(f"  {reason:<18}{nn:>10,}개  신고 {nr:>12,}")

    unknown = buckets["UNKNOWN"]
    curve, unk_total_reports = cutoff_curve(unknown)
    summarize_curve(curve, unk_total_reports)

    # 게이팅 한 줄 요약 (액션플랜 §5 Task 1 출력 목표)
    n_oos = len(buckets["OUT_OF_SCOPE"])
    n_bd = len(buckets["BRAND_DOSE"])
    n_unk = len(unknown)
    print("\n=== 게이팅 요약 ===")
    print(f"{total_names:,} 중 → class/비약물(OUT_OF_SCOPE) {n_oos:,}개, "
          f"브랜드+용량(BRAND_DOSE, 재조회 가능) {n_bd:,}개, "
          f"진짜 LLM 대상(UNKNOWN) {n_unk:,}개")
    print(f"→ Z(= 3-A Recall·GPU 산정 분모) = {n_unk:,}  (운영점 적용 전)")

    if args.out_unknown_csv:
        write_rows_csv(args.out_unknown_csv, unknown)
        print(f"UNKNOWN 저장 → {args.out_unknown_csv}")
    if args.out_oos_csv:
        write_rows_csv(args.out_oos_csv, buckets["OUT_OF_SCOPE"])
        print(f"OUT_OF_SCOPE 저장 → {args.out_oos_csv}")
    if args.out_curve_csv:
        write_curve_csv(args.out_curve_csv, curve)
        print(f"곡선 저장 → {args.out_curve_csv}")


if __name__ == "__main__":
    main()
