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

입력  : coverage.py --dump-miss 가 뽑은 miss CSV (name, n_reports)
사전  : build_map.py 가 만든 drug_ingredient_map.csv (gitignore, 별도 생성 필요)
출력  : 매칭 결과 CSV (아래 OUT_FIELDS) — method="유사도", confidence=유사도 점수

의존성: rapidfuzz (pip install rapidfuzz). 다른 drug_dict 스크립트는 stdlib만 쓰지만
        유사도 스코어러는 C++ 구현이 필요해 예외로 둔다(coverage.py의 duckdb와 같은 예외).

실행 예:
  python3 coverage.py --dump-miss miss.csv          # 1단계 실패분 뽑기(S3 필요)
  python3 similarity.py match miss.csv -o matched.csv --threshold 92
  python3 similarity.py sweep miss.csv -o curve.csv  # (후보수·임계값) 곡선
"""
import argparse
import csv
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


def run_match(miss_path, out_path, map_path, scorer_name, threshold, limit, margin):
    scorer = SCORERS[scorer_name]
    cands = load_candidates(map_path)
    miss = load_miss(miss_path)
    print(f"후보: {len(cands.records):,}개 (버킷 {len(cands.buckets)}개) / miss: {len(miss):,}개", flush=True)

    n_matched = n_ambiguous = n_low_margin = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        for name, n in miss:
            q, best, score, second = match_one(name, cands, scorer, limit)
            if best is None or score < threshold:
                continue
            if score - second < margin:      # 1·2등 초박빙 → 애매, 채택 보류(3단계로)
                n_low_margin += 1
                continue
            rec, ambiguous = cands.canonical(best)
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
    print(f"채택 {n_matched:,}개 (임계값 {threshold}, scorer={scorer_name}, "
          f"ambiguous {n_ambiguous:,}개) / 마진<{margin} 보류 {n_low_margin:,}개", flush=True)
    print(f"→ {out_path}", flush=True)


def run_sweep(miss_path, out_path, map_path, scorer_name, limit, gold_path):
    """임계값을 훑어가며 채택 수(계산량↔정확도 곡선)를 낸다. gold(정답지)가 있으면
    precision도 함께. gold 없이도 채택 수·점수 분포는 낼 수 있어 운영점 탐색에 쓴다."""
    scorer = SCORERS[scorer_name]
    cands = load_candidates(map_path)
    miss = load_miss(miss_path)
    gold = load_gold(gold_path) if gold_path else None
    print(f"스윕: miss {len(miss):,}개, scorer={scorer_name}"
          + (f", gold {len(gold):,}개" if gold else " (gold 없음 — precision 생략)"), flush=True)

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
    m.add_argument("--limit", type=int, default=5, help="블로킹 후 채점 상위 후보 수")
    m.add_argument("--margin", type=float, default=DEFAULT_MARGIN, help="1·2등 점수 차 하한(미만이면 보류)")

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
                  args.threshold, args.limit, args.margin)
    elif args.cmd == "sweep":
        run_sweep(args.miss, args.out, args.map, args.scorer, args.limit, args.gold)


if __name__ == "__main__":
    main()
