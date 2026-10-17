"""
C단계 3단계 전처리 — 브랜드+용량 재정규화 → 1단계 사전 재조회 (BRAND_DOSE, 이슈 #49)

3단계 설계 §2-2 [T] triage의 "브랜드+용량/제형 → 재정규화 → Stage 1 사전 재조회" 부품.
triage(PR#48)는 BRAND_DOSE를 1단계와 **같은** normalize_query로 재조회해서 실측 0개가
나온다 — 잔여는 정의상 그 정규화로 이미 실패한 이름들이기 때문이다.

잔여 상위를 보면 브랜드 자체는 사전에 있는데 뒤에 뭔가 붙어서 못 찾은 경우가 많다.
normalize_query는 **끝 토큰만** 반복해서 떼므로, 용량 뒤에 기기명이 오거나(HUMIRA 40 MG/0.8 ML PEN)
괄호가 끼면(OXYCONTIN (NDA 22-272)) 중간에서 멈춘다. 여기서는 그 앞단을 잘라 후보 쿼리를
여러 개 만들고, 각각을 정규화(normalize_query, 1단계와 동일)한 뒤 사전을 다시 찾는다.

  규칙(rule)         예                                          → 쿼리
  cut_strength      HUMIRA 40 MG/0.8 ML PEN                     → HUMIRA
  drop_paren        OXYCONTIN (NDA 22-272)                      → OXYCONTIN
  paren_inner       ZOMETA (ZOLEDRONIC ACID)                    → ZOLEDRONIC ACID
  drop_paren+cut    VICTOZA (LIRAGLUTIDE) SOLUTION ..., 6MG/ML  → VICTOZA
  cut_comma         GILENYA CAPSULE, 0.5MG                      → GILENYA
  device_tail       SYMBICORT PMDI                              → SYMBICORT
  slash_alt         LEPONEX / CLOZARIL                          → CLOZARIL (양쪽이 같은 성분일 때만)

precision 우선 (2단계 similarity.py와 같은 원칙) — 잘못 붙이면 B단계 집계가 조용히 오염된다.
그래서 후보 쿼리들이 **서로 다른 성분 묶음**을 가리키면 확정하지 않고 CONFLICT로 남긴다
(호출부가 UNKNOWN에 남겨 LLM 판정으로 넘긴다). 복합제일 수 있는 표기도 같은 이유로 확정하지 않는다:
  - "\\" 구분 복합제 → 1단계의 조합 exact-match 영역이라 여기서 건드리지 않음
  - " / " 양쪽 중 일부만 사전에 있음 → 복합제 일부만 붙을 위험
  - 괄호 안이 쉼표로 여러 개("VICODIN (HYDROCODONE BITARTRATE, PARACETAMOL)") → 각 부분이
    전부 사전에 있고 같은 성분으로 풀릴 때만 인정
  - 괄호 밖에 +·AND·WITH·W/ 같은 연결어("BUTALBITAL, ACETAMINOPHEN + CAFFEINE") → 한 성분만
    떼어 붙이지 않음

공통 normalize.py는 건드리지 않는다 — 고치면 1단계 커버리지 숫자가 바뀌고, 그건 팀 결정 사항이다
(캐스케이드 계약 회의 안건 D). 여기 규칙은 1단계 실패분에만 적용하는 별도 단계다.

triage 연결: make_dict_lookup()은 triage.load_dict_lookup()과 같은 name→bool 함수를 돌려준다.
triage의 BRAND_DOSE 판정에 그대로 꽂으면 된다.

사전 위치: silver/drug_dict/drug_ingredient_map.csv (팀 합의 경로 — match.py·build_map.py·similarity.py와 동일).
gitignore라 GitHub Release(dict-*)에서 받아 이 위치에 둔다. 파일이 없거나 dictionary_version이
build_map.DICTIONARY_VERSION과 다르면 에러로 멈춘다(존재만 보면 옛 사전으로 조용히 돌 수 있어서).

실행:
  python3 renormalize.py silver/drug_dict/unmatched_for_stage3.csv
  python3 renormalize.py silver/drug_dict/unmatched_for_stage3.csv \\
          --out-resolved-csv out/brand_dose_resolved.csv --out-conflict-csv out/brand_dose_conflict.csv
"""
import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from normalize import normalize_query, DOSAGE_FORM_WORDS
from build_map import DICTIONARY_VERSION, normalize_ingredient

DEFAULT_MAP = HERE / "drug_ingredient_map.csv"

# 숫자+단위 = 용량/농도 표기. 이 지점부터 뒤는 전부 용량·제형·기기 설명이라 통째로 자른다.
# 앞이 영문자인 숫자(PD4·B12·T4)는 제품명/지정자일 수 있어 제외한다.
# 복합제 용량은 숫자가 /·-로 이어진 뒤 단위가 한 번만 붙는다(TRIBENZOR 40/5/12.5 MG, HYCODAN 5-1.5 MG).
_UNIT = (r"(?:MG|MCG|UG|G|GM|KG|ML|L|MEQ|IU|UNITS?|%|MMOL|"
         r"MILLIGRAMS?|MICROGRAMS?|GRAMS?|MILLILITERS?|MILLILITRES?)")
_NUM = r"(?:\d+(?:[.,]\d+)?|[.,]\d+)"  # ".5 MG"처럼 앞 0을 생략한 소수 포함
STRENGTH = re.compile(r"(?<![A-Z\d])" + _NUM + r"(?:\s*[/-]\s*" + _NUM + r")*\s*" + _UNIT + r"(?![A-Z])")
_PAREN = re.compile(r"\(([^()]*)\)")
_WS = re.compile(r"\s+")

# normalize_query의 DOSAGE_FORM_WORDS에 없는 기기·제형 꼬리. 끝에서부터 반복해서 뗀다.
# 토큰이 하나만 남으면 멈춘다 — "SOLOSTAR"처럼 기기명 자체가 이름인 경우를 지우지 않기 위해.
DEVICE_TAIL_WORDS = frozenset({
    "PEN", "PENS", "KWIKPEN", "FLEXPEN", "FLEXTOUCH", "SOLOSTAR",
    "SYRINGE", "SYRINGES", "PRE-FILLED", "PREFILLED", "PFS",
    "AUTOINJECTOR", "AUTO-INJECTOR", "INJECTOR", "INJCTR", "CARTRIDGE", "VIAL", "VIALS",
    "INHALER", "PMDI", "MDI", "DISKUS", "RESPIMAT", "HANDIHALER", "NEBULIZER",
    "CHEWABLE", "COATED", "FILM-COATED", "TABLETS", "TABLET", "CAPSULES", "CAPSULE",
    "SOLUTION", "SUSPENSION", "INJECTION", "FOR", "ORAL", "CREAM", "OINTMENT",
})

# 괄호 안이 제형·투여경로 설명뿐이면("(NASAL SOLUTION)", "(FOR ORAL SOLUTION)") 성분 정보가 없다.
# 정규화하면 "NASAL" 같은 일반어만 남는데, 사전에 그런 이름의 제품(동종요법 등)이 있어 엉뚱한
# 성분으로 붙는다(실측). 이런 괄호는 paren_inner 후보로 쓰지 않는다.
_ROUTE_WORDS = frozenset({
    "NASAL", "EYE", "EAR", "OPHTHALMIC", "OTIC", "RECTAL", "VAGINAL", "SUBLINGUAL", "BUCCAL",
    "TRANSDERMAL", "INHALATION", "INHALED", "INTRAVENOUS", "INTRAMUSCULAR", "SUBCUTANEOUS",
    "DROPS", "DROP", "SPRAY", "GEL", "LOTION", "PATCH", "POWDER", "SYRUP", "LIQUID", "FILM",
})
_FORM_ONLY_WORDS = DOSAGE_FORM_WORDS | DEVICE_TAIL_WORDS | _ROUTE_WORDS


def _form_only(text: str) -> bool:
    tokens = [t for t in re.split(r"[^A-Z0-9%]+", text.upper()) if t]
    return bool(tokens) and all(t in _FORM_ONLY_WORDS or t.isdigit() for t in tokens)


@dataclass(frozen=True)
class Resolution:
    """재정규화 결과 한 건.

    status: RESOLVED(성분 묶음 하나로 확정) / CONFLICT(후보가 서로 다른 성분 → LLM으로) / MISS
    """
    status: str
    ingredient_set: str = ""
    matched_key: str = ""
    rule: str = ""
    candidates: tuple = field(default_factory=tuple)  # CONFLICT 사유 추적용 (쿼리, 규칙, 성분묶음들)


def load_key_index(map_path: Path = DEFAULT_MAP) -> dict[str, frozenset[str]]:
    """사전 → {조회키(대문자): 가리키는 ingredient_set 후보들}.

    match.load_dictionary와 같은 기준으로 브랜드명(medicinalproduct)과 성분명(ingredient_norm)
    둘 다 키로 쓴다. 다른 점은 bool이 아니라 **어떤 성분 묶음을 가리키는지**까지 들고 있다는 것 —
    후보끼리 성분이 갈리는지 봐야 하기 때문이다. ingredient_set의 " ; " 후보 보존(안건 B)도 풀어서 넣는다.

    키 종류마다 값을 다르게 잡는 건 의도된 것이다:
      - 제품명 키(ZOMETA, SYMBICORT) → 그 제품의 ingredient_set (복합제면 묶음 전체)
      - 성분명 키(AMLODIPINE)        → 그 성분 하나 = normalize_ingredient(ingredient_norm).lower()
    성분명 키에 그 행의 ingredient_set을 쓰면 안 된다 — 성분 행은 그 성분이 들어간 **제품**의 행이라,
    AMLODIPINE이 우연히 복합제(amlodipine|hydrochlorothiazide|olmesartan medoxomil) 행에서 오면
    "AMLODIPINE 5 MG"가 복합제로 붙는다(PR#43 리뷰에서 2단계 출력의 약 20%가 이 문제로 확인됨).
    단일 성분 제품에서는 두 값이 같은 normalize_ingredient를 거쳐 항상 일치한다(실측 55,419행 불일치 0).
    LEXAPRO 같은 CONFLICT는 이 차이가 아니라 소스 간 염 표기 차이(openFDA 'escitalopram oxalate' vs
    RxNorm 'escitalopram')에서 온다 — 사전 쪽 정리 대상."""
    index = defaultdict(set)
    with open(map_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            product = r["medicinalproduct"].strip().upper()
            ingr = r["ingredient_norm"].strip().upper()
            for cand in r["ingredient_set"].split(" ; "):
                cand = cand.strip()
                if cand and product:
                    index[product].add(cand)
            if ingr:
                index[ingr].add(normalize_ingredient(ingr).lower())
    return {k: frozenset(v) for k, v in index.items()}


def strip_device_tail(q: str) -> str:
    tokens = q.split()
    while len(tokens) > 1 and tokens[-1].strip(",") in DEVICE_TAIL_WORDS:
        tokens.pop()
    return " ".join(tokens)


def to_query(fragment: str) -> str:
    """잘라낸 조각 → 조회 쿼리. 1단계와 같은 normalize_query 후 기기·제형 꼬리를 추가로 뗀다."""
    fragment = fragment.strip(" ,;-/")
    if not fragment:
        return ""
    return strip_device_tail(normalize_query(fragment))


def candidate_queries(raw: str) -> list[tuple[str, str]]:
    """원본 이름에서 (쿼리, 규칙) 후보들을 만든다. 중복 쿼리는 처음 나온 규칙만 남긴다.
    " / " 대체명과 괄호 안 쉼표 목록은 여기서 만들지 않는다 — 부분 일치 위험이 있어
    resolve()에서 따로 "전부 같은 성분일 때만" 규칙으로 본다."""
    s = _WS.sub(" ", raw.strip().upper())
    out: list[tuple[str, str]] = []

    def add(fragment: str, rule: str) -> None:
        q = to_query(fragment)
        if q and all(q != seen for seen, _ in out):
            out.append((q, rule))

    m = STRENGTH.search(s)
    if m:
        add(s[:m.start()], "cut_strength")

    no_paren = _WS.sub(" ", _PAREN.sub(" ", s)).strip()
    if no_paren != s:
        add(no_paren, "drop_paren")
        m2 = STRENGTH.search(no_paren)
        if m2:
            add(no_paren[:m2.start()], "drop_paren+cut")
        for inner in _PAREN.findall(s):
            if "," not in inner and not _form_only(inner):
                add(inner, "paren_inner")

    if "," in no_paren:
        add(no_paren.split(",", 1)[0], "cut_comma")

    add(s, "device_tail")
    return out


# 복합제 표기 연결어. 이게 있으면 한 성분만 떼어 붙이는 건 복합제 일부만 보는 것이다
# (실측: "BUTALBITAL, ACETAMINOPHEN + CAFFEINE" → butalbital, "CALCIUM WITH VITAMIN D" → calcium).
_COMBO_MARKER = re.compile(r"\+|&|\bAND\b|\bW/|\bWITH\b")


def _comma_rest_is_ingredient(no_paren: str, index: dict) -> bool:
    """쉼표 뒤가 용량·제형 설명("ALLEGRA-D, 24 HR")이 아니라 성분·제품이면 True.
    "ALEVE CAPLET/TABLET,MIDOL ..."(다른 제품)이나 "FAMOTIDINE, CALCIUM CARBONATE AND ..."(복합제)에서
    쉼표 앞만 떼어 붙이면 일부만 보는 것이라 cut_comma를 쓰지 않는다."""
    if "," not in no_paren:
        return False
    rest = no_paren.split(",", 1)[1]
    if _COMBO_MARKER.search(rest):
        return True
    for part in re.split(r"[,/]", rest):
        q = to_query(part)
        if q and (q in index or any(tok in index for tok in q.split())):
            return True
    return False


def resolve(raw: str, index: dict[str, frozenset[str]]) -> Resolution:
    s = _WS.sub(" ", raw.strip().upper())
    if not s or "\\" in s:
        return Resolution("MISS")  # "\" 복합제는 1단계 조합 exact-match 영역

    hits: list[tuple[str, str, frozenset]] = []

    # " / " 대체명: 양쪽(전부)이 사전에 있고 같은 성분일 때만. 일부만 있으면 복합제 일부일 수 있다.
    if " / " in s:
        parts = [to_query(p) for p in s.split(" / ")]
        found = [p for p in parts if p in index]
        if found and len(found) < len(parts):
            return Resolution("CONFLICT", rule="slash_partial",
                              candidates=tuple((p, "slash_alt", tuple(sorted(index[p]))) for p in found))
        if found:
            hits.extend((p, "slash_alt", index[p]) for p in parts)

    # 괄호 안 쉼표 목록: 부분이 **전부** 사전에 있고 같은 성분일 때만 인정한다.
    # 일부만 있으면 복합제 일부일 수 있다(실측: "VICODIN (HYDROCODONE BITARTRATE, PARACETAMOL)"에서
    # PARACETAMOL이 미국 사전에 없어 hydrocodone 단일로 붙던 것). 용량·USP처럼 정규화 후 빈 조각은 무시.
    for inner in _PAREN.findall(s):
        if "," in inner:
            parts = [q for q in (to_query(p) for p in inner.split(",")) if q]
            found = [p for p in parts if p in index]
            if len(parts) > 1 and (len(found) < len(parts) or len({index[p] for p in found}) > 1):
                return Resolution("CONFLICT", rule="paren_list",
                                  candidates=tuple((p, "paren_list", tuple(sorted(index[p]))) for p in found))
            hits.extend((p, "paren_list", index[p]) for p in found)

    no_paren = _WS.sub(" ", _PAREN.sub(" ", s)).strip()
    skip_comma = _comma_rest_is_ingredient(no_paren, index)
    for q, rule in candidate_queries(s):
        if rule == "cut_comma" and skip_comma:
            continue
        if q in index:
            hits.append((q, rule, index[q]))

    # 괄호 밖에 복합제 연결어가 있으면, 연결어까지 포함한 이름이 통째로 사전 제품인 경우만 인정한다.
    if _COMBO_MARKER.search(no_paren):
        whole = [h for h in hits if _COMBO_MARKER.search(h[0])]
        if not whole:
            if hits:
                return Resolution("CONFLICT", rule="combo_marker",
                                  candidates=tuple((q, rule, tuple(sorted(st))) for q, rule, st in hits))
            return Resolution("MISS")
        hits = whole

    if not hits:
        return Resolution("MISS")
    sets = set().union(*(h[2] for h in hits))
    if len(sets) > 1:
        return Resolution("CONFLICT", rule="multi_set",
                          candidates=tuple((q, rule, tuple(sorted(st))) for q, rule, st in hits))
    q, rule, _ = hits[0]
    return Resolution("RESOLVED", ingredient_set=next(iter(sets)), matched_key=q, rule=rule)


def check_map(map_path: Path, expected_version: str = DICTIONARY_VERSION) -> None:
    """사전 파일이 있고 현재 코드가 기대하는 버전인지 확인한다. 아니면 이유와 함께 멈춘다."""
    if not map_path.exists():
        raise FileNotFoundError(
            f"사전 파일 없음: {map_path}\n"
            f"GitHub Release(dict-*)의 drug_ingredient_map.csv를 받아 {DEFAULT_MAP} 에 두거나 --map으로 지정")
    with open(map_path, newline="", encoding="utf-8") as f:
        versions = Counter(r.get("dictionary_version", "") for r in csv.DictReader(f))
    if set(versions) != {expected_version}:
        found = ", ".join(f"{v or '(빈값)'} {n:,}행" for v, n in versions.most_common())
        raise ValueError(
            f"사전 버전 불일치: {map_path}\n"
            f"  기대 {expected_version} (build_map.DICTIONARY_VERSION) / 파일 {found}\n"
            f"  최신 Release의 사전으로 교체할 것")


def make_dict_lookup(map_path: Path = DEFAULT_MAP):
    """triage.load_dict_lookup 대체용 name→bool. RESOLVED만 True(=BRAND_DOSE).
    CONFLICT는 False라 triage에서 UNKNOWN(LLM 대상)에 남는다."""
    check_map(map_path)
    index = load_key_index(map_path)
    return lambda name: resolve(name, index).status == "RESOLVED"


def load_rows(path: Path) -> list[tuple[str, int]]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            name = (r.get("name") or "").strip()
            if not name:
                continue
            try:
                n = int(r.get("n_reports") or 0)
            except ValueError:
                n = 0
            rows.append((name, n))
    return rows


def main():
    ap = argparse.ArgumentParser(description="브랜드+용량 재정규화 → 사전 재조회 (BRAND_DOSE)")
    ap.add_argument("input", type=Path, help="잔여 CSV (name,n_reports) — silver/drug_dict/unmatched_for_stage3.csv 등")
    ap.add_argument("--map", type=Path, default=DEFAULT_MAP, help=f"사전 CSV (기본 {DEFAULT_MAP})")
    ap.add_argument("--out-resolved-csv", type=Path, help="RESOLVED만 저장 (name,n_reports,matched_key,rule,ingredient_set)")
    ap.add_argument("--out-conflict-csv", type=Path, help="CONFLICT만 저장 (감사용)")
    args = ap.parse_args()

    check_map(args.map)
    index = load_key_index(args.map)
    rows = load_rows(args.input)
    total_reports = sum(n for _, n in rows)
    print(f"사전 조회키 {len(index):,}개 / 입력 {len(rows):,} 고유명, 신고합 {total_reports:,}")

    by_status = defaultdict(list)
    rule_count = Counter()
    for name, n in rows:
        res = resolve(name, index)
        by_status[res.status].append((name, n, res))
        if res.status == "RESOLVED":
            rule_count[res.rule] += 1

    print(f"\n{'상태':<10}{'고유명':>10}{'(%)':>8}{'신고합':>12}{'(%)':>8}")
    for st in ("RESOLVED", "CONFLICT", "MISS"):
        items = by_status[st]
        nn, nr = len(items), sum(n for _, n, _ in items)
        print(f"{st:<10}{nn:>10,}{nn / len(rows) * 100:>7.2f}%{nr:>12,}{nr / total_reports * 100:>7.2f}%")
    print("\nRESOLVED 규칙별:", ", ".join(f"{r} {c:,}" for r, c in rule_count.most_common()))

    print("\nRESOLVED 신고 상위 15:")
    for name, n, res in sorted(by_status["RESOLVED"], key=lambda x: -x[1])[:15]:
        print(f"  {n:>7,}  {name[:55]:<55} → {res.ingredient_set}  [{res.rule}]")

    if args.out_resolved_csv:
        args.out_resolved_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out_resolved_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["name", "n_reports", "matched_key", "rule", "ingredient_set"])
            for name, n, res in sorted(by_status["RESOLVED"], key=lambda x: -x[1]):
                w.writerow([name, n, res.matched_key, res.rule, res.ingredient_set])
        print(f"\nRESOLVED 저장 → {args.out_resolved_csv}")
    if args.out_conflict_csv:
        args.out_conflict_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out_conflict_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["name", "n_reports", "rule", "candidates"])
            for name, n, res in sorted(by_status["CONFLICT"], key=lambda x: -x[1]):
                cands = " ; ".join(f"{q}->{'|'.join(st)}" for q, _, st in res.candidates)
                w.writerow([name, n, res.rule, cands])
        print(f"CONFLICT 저장 → {args.out_conflict_csv}")


if __name__ == "__main__":
    main()
