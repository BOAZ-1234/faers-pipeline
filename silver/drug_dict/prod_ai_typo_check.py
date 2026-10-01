"""
C세부1 — prod_ai 회수분 진짜 오류(오타 등) 확인 (1장 액션플랜, PR#30 후속)

PR#30 리뷰 대응 실측(2026-09-22, normalize.py 주석)에서 "prod_ai도 사전 대조 필요"를
검토했을 때는 회수분 134,419개를 통째로 사전과 대조해 몇 %가 탈락하는지만 쟀다.
그 탈락분(17,946개, 13.35%)의 대다수가 TOZINAMERAN 같은 정식 INN명(사전이 작아서
못 찾는 정답)이었다는 것까지는 확인했지만, 그 안에 진짜 오타(사전에 있는 성분명이
한두 글자 잘못 적힌 경우)가 섞여 있는지는 개별로 들여다보지 않았다 — 이번 확인은
"사전에 없다"가 아니라 "사전에 있는 것과 글자가 비슷하다"를 기준으로 후보를 추린다.

resolve_prod_ai()로 정규화된 회수 토큰을 ingredient_norm 사전과 difflib로 유사도
비교해 세 그룹으로 나눈다:
  - exact:  사전에 정확히 있음 → 오류 아님(정상 회수)
  - near:   사전 항목과 글자가 비슷한데(cutoff 이상) 똑같지는 않음 → 오타 의심, 수동 검토 대상
  - novel:  사전 어떤 항목과도 안 비슷함 → 사전이 작아서 생기는 공백(기존 결론과 일치), 오류 아님

실행:
  python3 prod_ai_typo_check.py                       (S3 전체 스캔, 수 분 걸림)
  python3 prod_ai_typo_check.py --out-csv near.csv     (near 그룹 전체를 신고건수 내림차순 CSV로 저장)
"""
import argparse
import csv
import difflib
from pathlib import Path

from coverage import fetch_drugname_counts
from match import load_dictionary, lookup
from normalize import resolve_prod_ai

NEAR_CUTOFF = 0.80  # 이 이상이면 "글자가 비슷하다"로 본다 — 1~2글자 오타는 대개 0.85+


def classify_token(token: str, ingredients: set[str]) -> tuple[str, str, float]:
    """token(소문자, resolve_prod_ai 출력)을 ingredients(사전, 대문자)와 비교.
    반환: (그룹, 최근접 사전 항목, 유사도)"""
    upper = token.upper()
    if upper in ingredients:
        return "exact", upper, 1.0
    match = difflib.get_close_matches(upper, ingredients, n=1, cutoff=NEAR_CUTOFF)
    if match:
        ratio = difflib.SequenceMatcher(None, upper, match[0]).ratio()
        return "near", match[0], ratio
    return "novel", "", 0.0


def main():
    ap = argparse.ArgumentParser(description="C세부1 prod_ai 회수분 오타 의심 확인")
    ap.add_argument("--out-csv", type=Path, help="near 그룹 CSV 저장 경로(신고건수 내림차순)")
    args = ap.parse_args()

    print("FAERS drugname 집계 중 (S3 Iceberg 전체 스캔 — 수 분 걸림)...", flush=True)
    rows = fetch_drugname_counts()
    products, ingredients, known_combos = load_dictionary()
    print(f"고유 약물명: {len(rows):,}개, 사전 성분 {len(ingredients):,}개", flush=True)

    # near: (token, best_match, ratio) -> [(name, n), ...]  — 같은 토큰이 여러 drugname에서 나올 수 있음
    near: dict[tuple[str, str, float], list[tuple[str, int]]] = {}
    counts = {"exact": [0, 0], "near": [0, 0], "novel": [0, 0]}  # [고유명, 신고건]

    for name, n, pai in rows:
        if lookup(name, products, ingredients, known_combos):
            continue  # 1단계 사전 조회로 이미 잡힘 — prod_ai 회수 대상 아님
        resolved = resolve_prod_ai(pai)
        if resolved is None:
            continue  # 여전히 미매칭 — prod_ai 회수분이 아니라 완전 미스

        # 복합제는 "|"로 여러 성분 — 토큰마다 따로 분류(하나만 오타여도 잡아야 함)
        worst = "exact"  # exact < near < novel 순으로 더 의심스러운 쪽을 그 이름의 대표 분류로
        for token in resolved.split("|"):
            group, best_match, ratio = classify_token(token, ingredients)
            if group == "near":
                near.setdefault((token, best_match, round(ratio, 3)), []).append((name, n))
            rank = {"exact": 0, "near": 1, "novel": 2}
            if rank[group] > rank[worst]:
                worst = group
        counts[worst][0] += 1
        counts[worst][1] += n

    total_unique = sum(c[0] for c in counts.values())
    total_reports = sum(c[1] for c in counts.values())
    print(f"\nprod_ai 회수분 전체: 고유명 {total_unique:,}개 / 신고 {total_reports:,}건")
    for group in ("exact", "near", "novel"):
        u, r = counts[group]
        pct_u = u / total_unique * 100 if total_unique else 0
        pct_r = r / total_reports * 100 if total_reports else 0
        print(f"  {group:>6}: 고유명 {u:>7,} ({pct_u:5.2f}%)  신고건 {r:>9,} ({pct_r:5.2f}%)")

    near_rows = sorted(
        ((token, best, ratio, sum(n for _, n in items), items[0][0])
         for (token, best, ratio), items in near.items()),
        key=lambda x: -x[3],
    )

    print(f"\n오타 의심(near) 상위 20개 — token → 최근접 사전 항목 (유사도):")
    for token, best, ratio, n_reports, sample_name in near_rows[:20]:
        print(f"  {n_reports:>8,}  {token!r:35} → {best!r:35} ({ratio:.2f})  예: {sample_name}")

    if args.out_csv:
        args.out_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["prod_ai_token", "closest_dict_ingredient", "similarity", "n_reports", "sample_drugname"])
            for token, best, ratio, n_reports, sample_name in near_rows:
                writer.writerow([token, best, ratio, n_reports, sample_name])
        print(f"\nnear 그룹 CSV 저장 → {args.out_csv} ({len(near_rows):,}개)")


if __name__ == "__main__":
    main()
