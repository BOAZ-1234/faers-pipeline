"""Stage 3 입력 중 SapBERT∪fuzzy K=10이 못 잡은 쿼리를 뽑아 패턴 분석.

목적: corpus 보강 전략 결정 — 어떤 변형이 많이 빠지는지 (브랜드/외국어/오타/복합제 등)
     Top-10 후보까지 보여줘 "왜 못 잡았나"를 눈으로 확인.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from eval_recall import (
    DEV_DEFAULT,
    UNMATCHED_DEFAULT,
    MODEL_IDS,
    QUERY_PREFIX,
    KS,
    _covers,
    fuzzy_top_isets,
    load_corpus,
    load_dev,
    load_stage3_names,
)


def classify(raw: str) -> str:
    """못 잡은 쿼리의 쉬운 분류."""
    r = raw.strip()
    rl = r.lower()
    if any(ord(ch) > 127 for ch in r):
        return "외국어/비ASCII"
    if " + " in r or "+" in r and " " in r:
        return "복합제(+ 표기)"
    if "/" in r:
        return "복합제(/ 표기)"
    if "(" in r and ")" in r:
        return "괄호 포함"
    if any(w in rl for w in ["tablet", "capsule", "injection", "cream", "patch", "spray", "drops",
                              "solution", "suspension", "ointment", "powder", "syrup"]):
        return "제형 포함"
    if any(ch.isdigit() for ch in r):
        return "숫자/용량"
    if len(r.split()) >= 3:
        return "다토큰(3+)"
    if len(r) <= 4:
        return "짧은이름"
    return "기타"


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=Path,
                    default=HERE / "output" / "faiss_sapbert.index")
    ap.add_argument("--corpus", type=Path,
                    default=HERE / "output" / "alias_corpus_sapbert.jsonl")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--n-samples", type=int, default=30, help="눈으로 볼 샘플 개수")
    ap.add_argument("--out-csv", type=Path,
                    default=HERE / "output" / "stage3_misses.csv")
    args = ap.parse_args()

    import faiss
    from sentence_transformers import SentenceTransformer

    gold = load_dev(DEV_DEFAULT)
    stage3 = load_stage3_names(UNMATCHED_DEFAULT)
    queries = [q for q in gold if q.upper() in stage3]
    gold = {q: gold[q] for q in queries}
    print(f"Stage 3 입력 교집합: {len(queries)}개")

    print("SapBERT 로드·검색...")
    model = SentenceTransformer(MODEL_IDS["sapbert"])
    index = faiss.read_index(str(args.index))
    corpus, _ = load_corpus(args.corpus)
    vecs = model.encode([QUERY_PREFIX["sapbert"] + q for q in queries],
                        batch_size=256, show_progress_bar=False,
                        normalize_embeddings=True, convert_to_numpy=True).astype("float32")
    _, indices = index.search(vecs, args.k)
    embed_top = {}
    for i, q in enumerate(queries):
        embed_top[q] = [corpus[idx]["ingredient_set"] for idx in indices[i] if idx != -1]
        # 눈으로 볼 때 alias도 같이
    embed_alias = {}
    for i, q in enumerate(queries):
        embed_alias[q] = [(corpus[idx]["alias"], corpus[idx]["ingredient_set"])
                          for idx in indices[i] if idx != -1]

    print("fuzzy 검색...")
    fuzzy_top = fuzzy_top_isets(queries, args.k)

    # 못 잡은 쿼리 추출
    misses = []
    for q in queries:
        expected = gold[q]
        merged = embed_top[q][:args.k] + fuzzy_top.get(q, [])[:args.k]
        if not _covers(merged, expected):
            misses.append(q)

    print(f"\n못 잡은 쿼리: {len(misses)}/{len(queries)} = {len(misses)/len(queries)*100:.1f}%")

    # 분류 분포
    buckets: dict[str, list[str]] = {}
    for q in misses:
        buckets.setdefault(classify(q), []).append(q)

    print("\n분류별 분포:")
    for label, qs in sorted(buckets.items(), key=lambda x: -len(x[1])):
        print(f"  {label:<18} {len(qs):>4}개 ({len(qs)/len(misses)*100:>4.1f}%)")

    # 샘플 보기 (분류별 2~3개씩)
    print(f"\n=== 분류별 샘플 (각 분류 최대 3개, 전체 {args.n_samples}개까지) ===")
    shown = 0
    for label, qs in sorted(buckets.items(), key=lambda x: -len(x[1])):
        if shown >= args.n_samples:
            break
        print(f"\n[{label}] {len(qs)}개 중 샘플:")
        for q in qs[:3]:
            if shown >= args.n_samples:
                break
            expected = sorted(gold[q])
            print(f"\n  쿼리: {q!r}")
            print(f"  정답: {expected}")
            print(f"  SapBERT Top-{args.k}:")
            for alias, iset in embed_alias[q][:args.k]:
                print(f"    - {alias} → {iset}")
            print(f"  fuzzy Top-{args.k}:")
            for iset in fuzzy_top.get(q, [])[:args.k]:
                print(f"    - {iset}")
            shown += 1

    # 전체 miss CSV 저장
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["raw_drug_name", "expected_ingredients", "category",
                    "sapbert_top1_alias", "sapbert_top1_iset", "fuzzy_top1_iset"])
        for q in misses:
            top_alias = embed_alias[q][0] if embed_alias[q] else ("", "")
            top_fuzzy = fuzzy_top.get(q, [""])[0] if fuzzy_top.get(q) else ""
            w.writerow([q, "|".join(sorted(gold[q])), classify(q),
                        top_alias[0], top_alias[1], top_fuzzy])
    print(f"\n전체 miss → {args.out_csv}")


if __name__ == "__main__":
    main()
