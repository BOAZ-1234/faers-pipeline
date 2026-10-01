"""
C세부1 — 2단계 글자 유사도 매칭 (§4-2 [2단계])

1단계(정규화+사전 exact 조회, match.py)와 3단계(임베딩+LLM 판정) 사이 단계.
1단계에서 못 잡은 자유기재 약물명을, drug_ingredient_map의 브랜드명/성분명과
**순수 문자열 유사도**로 대조해 흡수한다. 오타·어순·접미사 변형을 잡는 게 목적
(예: "AMOXICILLIN TRIHYD" ↔ "AMOXICILLIN TRIHYDRATE"에서 잘린 접미사).

설계 원칙
- **precision 우선**: 잘못 붙이면 B단계 집계가 오염된다. 애매하면 채택하지 않고
  3단계(GPU)로 넘긴다. 그래서 임계값은 높게, 1·2등 점수 차가 작으면 보류한다.
- **키 계약**: 정규화는 1단계와 **똑같은** normalize_query를 쓰고, 출력 canonical은
  매칭된 사전 행의 ingredient_norm/ingredient_set를 **그대로** 가져온다. 단계마다
  다른 정규화를 쓰면 채점기 조인이 조용히 깨진다(캐스케이드 계약 회의 안건 D).
- **blocking**: 정규화 쿼리의 첫 글자로 후보를 나눠, 같은 버킷 안에서만 유사도를
  잰다(전량 O(N·M) 회피). 첫 글자 오타는 이 방식이 놓치지만, precision 우선이라
  감수한다(그런 건 3단계로).

입력  : 1단계+1.5(prod_ai 폴백) 파이프라인이 뽑은 miss CSV (name, n_reports)
사전  : build_map.py 가 만든 drug_ingredient_map.csv (gitignore, 별도 생성 필요)
출력  : 매칭 결과 CSV (아래 OUT_FIELDS) — method="유사도", confidence=유사도 점수

의존성: rapidfuzz (pip install rapidfuzz). 다른 drug_dict 스크립트는 stdlib만 쓰지만
        유사도 스코어러는 C++ 구현이 필요해 예외로 둔다(coverage.py의 duckdb와 같은 예외).

실행 예:
  python3 similarity.py match miss.csv -o matched.csv --threshold 92
  python3 similarity.py sweep miss.csv -o curve.csv  # (후보수·임계값) 곡선
"""
import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz, process

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from normalize import normalize_query
from build_map import DICTIONARY_VERSION

DEFAULT_MAP = HERE / "drug_ingredient_map.csv"

SCORERS = {
    "token_sort_ratio": fuzz.token_sort_ratio,  # 어순 무시(기본, 보수적)
    "WRatio": fuzz.WRatio,                       # 부분/토큰 조합(더 관대)
    "ratio": fuzz.ratio,                         # 순수 편집거리 비율
}

OUT_FIELDS = [
    "drugname_raw", "normalized", "n_reports",
    "matched_candidate", "match_field", "score", "second_score", "ambiguous",
    "medicinalproduct", "ingredient_norm", "ingredient_set", "unii",
    "method", "confidence", "dictionary_version",
]

# 1·2등 점수 차가 이보다 작으면 "어느 쪽인지 애매"로 보고 채택하지 않는다(→ 3단계).
DEFAULT_MARGIN = 3.0


def block_key(s: str) -> str:
    """블로킹 키 = 알파벳순으로 가장 앞선 토큰의 첫 글자.

    단순히 '문자열의 첫 글자'로 나누면 어순 변형("LISINOPRIL HYDROCHLOROTHIAZIDE"
    ↔ "HYDROCHLOROTHIAZIDE LISINOPRIL")이 서로 다른 버킷에 떨어져 못 잡는다
    (§4-2가 잡으라고 한 케이스). 토큰을 정렬한 뒤 첫 토큰의 첫 글자를 쓰면 어순이
    달라도 같은 버킷에 모인다. 첫 토큰 자체에 오타가 난 경우는 이 방식이 놓치지만,
    precision 우선이라 감수한다(그런 건 3단계로)."""
    toks = s.split()
    return min(toks)[0] if toks else ""


class Candidates:
    """사전에서 뽑은 매칭 후보. 후보 문자열(대문자)마다 원래 사전 행(canonical)을 건다.

    후보는 두 칼럼에서 나온다: medicinalproduct(브랜드) + ingredient_norm(성분).
    match.py가 둘 다 조회 대상으로 삼는 것과 동일(신고자가 성분명을 그대로 적기도 함)."""

    def __init__(self):
        self.records: dict[str, list[dict]] = defaultdict(list)  # cand(대문자) -> [행,...]
        self.buckets: dict[str, list[str]] = defaultdict(list)   # 첫글자 -> [cand,...]

    def add(self, cand: str, field: str, row: dict):
        cand = cand.strip().upper()
        if not cand:
            return
        if cand not in self.records:
            self.buckets[block_key(cand)].append(cand)
        self.records[cand].append({
            "match_field": field,
            "medicinalproduct": row.get("medicinalproduct", ""),
            "ingredient_norm": row.get("ingredient_norm", ""),
            "ingredient_set": row.get("ingredient_set", ""),
            "unii": row.get("unii", ""),
            # 매칭에 실제로 쓴 사전 행의 버전을 그대로 물려준다(재현성 추적 — build_map
            # docstring). 사전에 값이 없으면 build_map의 현재 상수로 폴백.
            "dictionary_version": row.get("dictionary_version") or DICTIONARY_VERSION,
        })

    def canonical(self, cand: str) -> tuple[dict, bool]:
        """후보 문자열 → (대표 canonical 행, ambiguous 여부).
        같은 문자열이 서로 다른 ingredient_norm으로 등록돼 있으면 ambiguous(브랜드
        하나가 성분 여러 개로 매핑되는 등) — 대표는 첫 행, 플래그를 세워 하류에 알린다."""
        rows = self.records[cand]
        distinct = {r["ingredient_norm"] for r in rows}
        return rows[0], len(distinct) > 1


def load_candidates(map_path: Path = DEFAULT_MAP) -> Candidates:
    c = Candidates()
    with open(map_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("medicinalproduct", "").strip():
                c.add(row["medicinalproduct"], "product", row)
            if row.get("ingredient_norm", "").strip():
                c.add(row["ingredient_norm"], "ingredient", row)
    return c


def load_miss(miss_path: Path) -> list[tuple[str, int]]:
    out = []
    with open(miss_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            n = int(row["n_reports"]) if row.get("n_reports") else 0
            out.append((row["name"], n))
    return out


def match_one(name: str, cands: Candidates, scorer, limit: int):
    """정규화 → 첫글자 블로킹 → 유사도 상위 후보. 채택 판단은 호출부(임계값·마진).

    반환: (normalized, best_cand, best_score, second_score) — 버킷이 비면 best_cand=None."""
    q = normalize_query(name)
    if not q:
        return "", None, 0.0, 0.0
    choices = cands.buckets.get(block_key(q))
    if not choices:
        return q, None, 0.0, 0.0
    # rapidfuzz는 버킷 전체를 C++로 빠르게 채점, 상위 limit개 반환(내림차순)
    hits = process.extract(q, choices, scorer=scorer, limit=max(limit, 2))
    best_cand, best_score = hits[0][0], hits[0][1]
    second_score = hits[1][1] if len(hits) > 1 else 0.0
    return q, best_cand, float(best_score), float(second_score)


def is_short_name(q: str, short_maxlen: int) -> bool:
    """짧은 단일토큰 이름인가 — 이런 이름은 한두 글자 차이로도 유사도가 높게 나와
    ("INSULIN"↔"INULIN"=92.3, 한 글자 차) 오매칭 위험이 크다. 그래서 더 높은
    임계값을 요구한다. 공백이 있으면(=여러 토큰) 정보량이 충분하다고 보고 제외."""
    return len(q) <= short_maxlen and " " not in q


def _alnum(s: str) -> str:
    """영숫자만 남기고 대문자화 — 특수문자/공백만 다른지 비교용."""
    return "".join(ch for ch in s if ch.isalnum()).upper()


# 변형 지정자: 문자+숫자(B6·B12·D3), 그리고 로마숫자(II·VII·IX…).
# 로마숫자는 응고인자(FACTOR I/II/VII/IX)처럼 번호가 곧 다른 약을 뜻하는 경우가 있어,
# 후보 번호가 원본에 없으면 다른 약으로 본다. 단독 I·V·X는 오탐이 커서 제외(2글자 이상만).
_DESIG = re.compile(r"\b(?:[A-Z]\d+|VIII|VII|XIII|XII|III|II|IV|IX|VI|XI)\b")

# 원본에서 '단독' 글자와 숫자가 비영숫자(공백·하이픈·깨진 '?' 등)로 떨어진 것만 결합한다
# ('B 12'·'B-12'·'B?12' → 'B12'). 단어 끝 글자는 \b가 없어 결합되지 않는다('TAB 12'의 B).
# 과거 _alnum()이 공백을 전부 지워 "TAB12"→"B12"로 오검출하던 버그 수정(PR 리뷰 코멘트 반영).
_LETTER_NUM_GAP = re.compile(r"\b([A-Z])[^A-Z0-9]+(\d+)")


def variant_designator_mismatch(raw: str, cand: str) -> bool:
    """후보에 든 번호 지정자(B6·B12·D3·FACTOR IX…)가 원본 이름에 없으면 True(=오변형).

    normalize_query가 용량으로 오인해 뒤 숫자를 떼면('VITAMIN B 12'→'VITAMIN B')
    포괄명 'VITAMIN B'가 사전의 특정 'VITAMIN B6'에 붙어 B12를 B6로 뭉갠다. 응고인자도
    'FACTOR I'가 'FACTOR IX'에 붙는 식(감사에서 확인). 정규화가 숫자를 떼기 전 원본(raw)을
    봐서, 후보의 번호가 원본에 없으면 서로 다른 약으로 보고 채택을 막는다.
    ('PARAGARD 380A'의 '380A'는 숫자+문자라 이 패턴에 안 걸려 오작동 없음. 단독 숫자
    용량(20MG 등)은 지정자로 안 보므로 브랜드→성분 매칭을 방해하지 않는다.)

    지정자 유형별로 원본 존재 여부를 다르게 본다(_alnum 전체결합 버그 수정):
    - 글자+숫자(B12·D3): 앞 글자가 '단독'이어야 하므로 왼쪽에 다른 글자가 붙으면 무효
      ('TAB 12'→'TAB12'의 'B12'는 기각). 'B 12'·'B?12'는 위에서 'B12'로 결합돼 인정.
    - 로마숫자(III·IX): 단어에 붙어 있어도 유효('ANTITHROMBINIII'의 III 인정)."""
    raw_norm = _LETTER_NUM_GAP.sub(r"\1\2", raw.upper())
    for tok in set(_DESIG.findall(cand.upper())):
        if tok[:1].isalpha() and tok[1:2].isdigit():          # 글자+숫자형(B12 등)
            found = re.search(r"(?<![A-Z])" + re.escape(tok), raw_norm) is not None
        else:                                                 # 로마숫자형(III·IX 등)
            found = tok in raw_norm
        if not found:
            return True
    return False


# 정규화가 활성성분의 일부(염·수식어)를 떼어내 남은 '조각'이 사전에 stub으로 존재하는 경우,
# 그 조각은 실제 약이 아니므로 채택하지 않는다. 예: DIMETHYL FUMARATE에서 SALT_SUFFIXES의
# FUMARATE가 떨어져 'DIMETHYL'이 되고 사전의 stub 'DIMETHYL'에 붙는다(감사에서 확인).
# 새 사례가 나오면 여기에 추가(근본 해결은 사전 정제/normalize 예외).
DEGENERATE_TARGETS = {"DIMETHYL"}


# 치료군(class)·제형·투여경로·상태/수식어 단어 목록. 자유기재명이 "이것만으로" 이뤄져
# 특정 성분 토큰이 하나도 없으면(예: "ANTIHISTAMINES", "EYE DROPS", "LAXATIVE",
# "COUGH DROP", "STOOL SOFTENER"), 유사도로 사전의 class/제형 stub에 붙어 단일 성분
# (ANTIHISTAMINES→DIPHENHYDRAMINE 등)으로 오매칭된다. stage2_suspicious.csv 감사(CLASS
# 플래그 ~69건, 최다 볼륨 ANTIHISTAMINE≈1,100신고)에서 확인된 오탐 — 근본원인은 사전에
# class/제형 stub이 product로 들어있는 것이라 2단계에서 입력 쪽을 막는다(사전 정제는 별건).
# 실제 성분/브랜드 토큰이 하나라도 남으면("FLUTICASONE PROPIONATE NASAL SPRAY"의
# FLUTICASONE, "SODIUM HYALURONATE EYE DROP"의 SODIUM) 통과시킨다 — precision 우선, 애매하면 3단계로.
STRUCTURE_WORDS = frozenset({
    # 치료군/범주 (class)
    "ANTIHISTAMINE", "ANTIHISTAMINES", "ANTIHISTAMIN", "ANTIHISTAMINNE",
    "ANTACID", "ANTACIDS", "LAXATIVE", "LAXATIVES",
    "DECONGESTANT", "DECONGESTANTS", "ANALGESIC", "ANALGESICS",
    "ANTIDIARRHEAL", "DIARRHEAL", "DIARRHEA", "DIARRHE",
    "ALLERGY", "MEDICINE", "MEDICINES", "MEDICATED",
    # 제형/투여경로/부위
    "EYE", "EAR", "NASAL", "ORAL", "TOPICAL", "OPHTHALMIC",
    "DROP", "DROPS", "DROPOS", "SPRAY", "CREAM", "CREAMS", "OINTMENT",
    "LOTION", "GEL", "SOLUTION", "SOLN", "SUSPENSION", "SUSP", "SYRUP",
    "INJECTION", "INJECTABLE", "PATCH", "TABLET", "TABLETS", "TAB", "TABS",
    "CAPSULE", "CAPSULES", "CAP", "CAPS", "LOZENGE", "LOZENGES", "POWDER", "OIL",
    "SUPPOSITORY", "SOFTENER", "SOFTENERS", "STOOL", "COUGH",
    "LUBRICANT", "LUBRICANTS", "LUBRICATING", "SOFT", "CHEW", "CHEWS",
    # 상태/수식어
    "ARTHRITIS", "ECZEMA", "ITCH",
    "WOMENS", "WOMEN", "MENS", "CHILDRENS", "CHILDREN", "INFANTS", "INFANT",
    "GENTLE", "NATURAL", "HERBAL", "DAILY", "MAXIMUM", "STRENGTH", "EXTRA",
    "HOUR", "HOURS", "HR", "NIGHT", "NIGHTTIME", "NIGHTIME", "DAY",
    "RELIEF", "RELEASE", "NOS", "ANTI",
})

_TOKEN_SPLIT = re.compile(r"[^A-Z0-9]+")


def class_or_form_only(name: str) -> bool:
    """자유기재명이 치료군·제형·수식어 단어로만 이뤄졌는지(=특정 성분 토큰 없음).
    True면 유사도로 사전의 class/제형 stub에 붙더라도 채택하지 않는다(→3단계).
    숫자 전용 토큰(용량·"12 HOUR")과 한 글자 토큰은 무의미로 보고 무시한다.
    원본(raw) 기준으로 판단한다 — normalize_query가 일부 제형어를 이미 떼어 판단이
    흔들리지 않게."""
    toks = [t for t in _TOKEN_SPLIT.split(name.upper()) if len(t) >= 2]
    meaningful = [t for t in toks if t not in STRUCTURE_WORDS and not t.isdigit()]
    return bool(toks) and not meaningful


def run_match(miss_path, out_path, map_path, scorer_name, threshold, limit, margin,
              short_threshold=96.0, short_maxlen=10, keep_ambiguous=False):
    scorer = SCORERS[scorer_name]
    cands = load_candidates(map_path)
    miss = load_miss(miss_path)
    print(f"후보: {len(cands.records):,}개 (버킷 {len(cands.buckets)}개) / miss: {len(miss):,}개", flush=True)
    print(f"임계값: 기본 {threshold}, 짧은이름(<={short_maxlen}자·단일토큰) {short_threshold}"
          f", ambiguous(복합제)={'채택' if keep_ambiguous else '보류→3단계'}", flush=True)

    n_matched = n_ambiguous = n_low_margin = n_short_reject = 0
    n_variant_reject = n_ambiguous_held = n_degenerate = n_class_reject = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        for name, n in miss:
            q, best, score, second = match_one(name, cands, scorer, limit)
            if best is None:
                continue
            # 특수문자·공백만 다르고 글자가 완전히 같으면(SOLU-MEDROL=SOLUMEDROL,
            # PRO-AIR=PROAIR) 오매칭일 수 없으니 짧은이름 규칙을 면제하고 기본 임계값만 본다.
            punct_variant = _alnum(q) == _alnum(best)
            # 짧은 단일토큰이면(단 위 예외 아니면) 더 높은 임계값을 적용(오매칭 방지)
            if is_short_name(q, short_maxlen) and not punct_variant:
                eff_threshold = short_threshold
            else:
                eff_threshold = threshold
            if score < eff_threshold:
                # 기본 임계값은 넘었는데 짧은이름 규칙에서 걸린 경우만 따로 센다
                if score >= threshold and eff_threshold > threshold:
                    n_short_reject += 1
                continue
            if score - second < margin:      # 1·2등 초박빙 → 애매, 채택 보류(3단계로)
                n_low_margin += 1
                continue
            # 치료군/제형만 있고 특정 성분이 없는 이름(ANTIHISTAMINES·EYE DROPS·LAXATIVE…)은
            # 사전의 class/제형 stub에 붙어 단일 성분으로 오매칭되므로 기각 → 3단계로(감사 반영).
            if class_or_form_only(name):
                n_class_reject += 1
                continue
            # 비타민류 번호 변형 불일치(VITAMIN B-12 → VITAMIN B6 등)면 기각 → 3단계로.
            # normalize가 숫자를 떼기 전 원본(name)으로 판단한다.
            if variant_designator_mismatch(name, best):
                n_variant_reject += 1
                continue
            rec, ambiguous = cands.canonical(best)
            # 정규화로 성분 조각만 남아 사전 stub에 붙은 것(DIMETHYL 등)은 실제 약이 아니라 기각.
            if rec["ingredient_norm"].strip().upper() in DEGENERATE_TARGETS:
                n_degenerate += 1
                continue
            # 후보가 복합제라 성분이 여럿(ambiguous)이면 단일 성분으로 우기지 않고
            # 보류→3단계(precision 우선). --keep-ambiguous면 종전대로 대표성분 채택.
            if ambiguous and not keep_ambiguous:
                n_ambiguous_held += 1
                continue
            if ambiguous:
                n_ambiguous += 1
            n_matched += 1
            w.writerow({
                "drugname_raw": name, "normalized": q, "n_reports": n,
                "matched_candidate": best, "match_field": rec["match_field"],
                "score": round(score, 1), "second_score": round(second, 1),
                "ambiguous": int(ambiguous),
                "medicinalproduct": rec["medicinalproduct"],
                "ingredient_norm": rec["ingredient_norm"],
                "ingredient_set": rec["ingredient_set"],
                "unii": rec["unii"],
                "method": "유사도",
                "confidence": round(score / 100, 4),
                "dictionary_version": rec["dictionary_version"],
            })
    print(f"채택 {n_matched:,}개 (scorer={scorer_name}, ambiguous {n_ambiguous:,}개)", flush=True)
    print(f"  보류/기각 → 마진<{margin}: {n_low_margin:,} / 짧은이름: {n_short_reject:,} / "
          f"번호변형불일치: {n_variant_reject:,} / 복합제(ambiguous): {n_ambiguous_held:,} / "
          f"불량stub: {n_degenerate:,} / class·제형만: {n_class_reject:,}", flush=True)
    print(f"→ {out_path}", flush=True)


def run_sweep(miss_path, out_path, map_path, scorer_name, limit, gold_path):
    """임계값을 훑어가며 채택 수(계산량↔정확도 곡선)를 낸다. gold(정답지)가 있으면
    precision도 함께. gold 없이도 채택 수·점수 분포는 낼 수 있어 운영점 탐색에 쓴다."""
    scorer = SCORERS[scorer_name]
    cands = load_candidates(map_path)
    miss = load_miss(miss_path)
    gold = load_gold(gold_path) if gold_path else None
    print(f"스윕: miss {len(miss):,}개, scorer={scorer_name}"
          + (f", gold {len(gold):,}개" if gold else " (gold 없음, precision 생략)"), flush=True)

    # 한 번만 채점해두고 임계값만 바꿔가며 집계
    scored = []  # (name, n, best_cand, score)
    for name, n in miss:
        q, best, score, _ = match_one(name, cands, scorer, limit)
        if best is not None:
            scored.append((name, n, best, score))

    thresholds = [t / 10 for t in range(800, 1000, 5)]  # 80.0 ~ 99.5, 0.5 간격
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        cols = ["threshold", "n_matched", "reports_matched"]
        if gold:
            cols += ["gold_n", "gold_correct", "precision"]
        w = csv.writer(f)
        w.writerow(cols)
        for t in thresholds:
            n_matched = reports = 0
            g_n = g_correct = 0
            for name, n, best, score in scored:
                if score < t:
                    continue
                n_matched += 1
                reports += n
                if gold and name in gold:
                    g_n += 1
                    rec, _ = cands.canonical(best)
                    if _norm_ing(rec["ingredient_norm"]) == _norm_ing(gold[name]):
                        g_correct += 1
            row = [t, n_matched, reports]
            if gold:
                prec = round(g_correct / g_n, 4) if g_n else ""
                row += [g_n, g_correct, prec]
            w.writerow(row)
    print(f"→ {out_path}  (임계값별 채택 수{'·precision' if gold else ''})", flush=True)


def _norm_ing(s: str) -> str:
    return (s or "").strip().upper()


def load_gold(gold_path: Path) -> dict[str, str]:
    """DiAna 등 외부 정답지: name → 정답 ingredient_norm.
    기대 칼럼: name, ingredient_norm. (DiAna 원본 스키마→이 형식 정렬은 별도 TODO —
    [[faers-normalization-prior-art]]의 DiAna 공개사전을 held-out 평가셋으로 씀.)"""
    gold = {}
    with open(gold_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            gold[row["name"]] = row["ingredient_norm"]
    return gold


def build_parser():
    p = argparse.ArgumentParser(description="2단계 글자 유사도 매칭 (§4-2)")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("match", help="miss를 유사도로 매칭해 canonical 출력")
    m.add_argument("miss", type=Path, help="coverage.py --dump-miss 결과 CSV")
    m.add_argument("-o", "--out", type=Path, default=HERE / "similarity_matched.csv")
    m.add_argument("--map", type=Path, default=DEFAULT_MAP)
    m.add_argument("--scorer", choices=SCORERS, default="token_sort_ratio")
    m.add_argument("--threshold", type=float, default=92.0, help="채택 최소 점수(precision 우선)")
    m.add_argument("--short-threshold", type=float, default=96.0,
                   help="짧은 단일토큰 이름의 최소 점수(오매칭 방지 — INSULIN↔INULIN류)")
    m.add_argument("--short-maxlen", type=int, default=10,
                   help="이 길이 이하 & 단일토큰이면 짧은 이름으로 보고 --short-threshold 적용")
    m.add_argument("--limit", type=int, default=5, help="블로킹 후 채점 상위 후보 수")
    m.add_argument("--margin", type=float, default=DEFAULT_MARGIN, help="1·2등 점수 차 하한(미만이면 보류)")
    m.add_argument("--keep-ambiguous", action="store_true",
                   help="복합제(성분 여럿) 매칭을 대표성분 하나로 채택(기본: 보류→3단계)")

    s = sub.add_parser("sweep", help="(임계값 → 채택 수/precision) 곡선")
    s.add_argument("miss", type=Path)
    s.add_argument("-o", "--out", type=Path, default=HERE / "similarity_sweep.csv")
    s.add_argument("--map", type=Path, default=DEFAULT_MAP)
    s.add_argument("--scorer", choices=SCORERS, default="token_sort_ratio")
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--gold", type=Path, help="정답지 CSV(name,ingredient_norm) — 있으면 precision 계산")
    return p


def main():
    args = build_parser().parse_args()
    if args.cmd == "match":
        run_match(args.miss, args.out, args.map, args.scorer,
                  args.threshold, args.limit, args.margin,
                  args.short_threshold, args.short_maxlen, args.keep_ambiguous)
    elif args.cmd == "sweep":
        run_sweep(args.miss, args.out, args.map, args.scorer, args.limit, args.gold)


if __name__ == "__main__":
    main()
