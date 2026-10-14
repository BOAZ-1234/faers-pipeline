"""
Stage 3-A — Recall@K 측정

build_index.py로 만든 FAISS 인덱스에 diana_golden_dev.csv의 raw_drug_name을 쿼리해
Recall@1/5/10/20 곡선을 출력한다. E5 vs SapBERT 비교에 쓴다.

평가 방식:
  쿼리: raw_drug_name (정규화 없음 — Stage 0 normalize_query는 캐스케이드가 적용할 것이고
  여기선 "첫 접촉" 시나리오를 측정하는 것이라 원본 그대로 넣는다)
  정답: ingredient_norm이 Top-K 후보 중 어느 ingredient_set에 포함되면 hit

  약물명 단위로 계산 (같은 raw_drug_name의 성분이 여러 개인 복합제는
  모든 성분이 Top-K 안에 있어야 hit — AND 기준).

실행:
  python3 eval_recall.py --index output/faiss_e5.index --corpus output/alias_corpus.jsonl
  python3 eval_recall.py --index output/faiss_sapbert.index --corpus output/alias_corpus.jsonl \\
      --model sapbert --out-csv output/recall_sapbert.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

UNMATCHED_DEFAULT = HERE.parent.parent / "silver" / "reaction_dict" / "output" / "unmatched_for_stage3.csv"
DEV_DEFAULT = HERE / "golden" / "output" / "diana_golden_dev.csv"
KS = [1, 5, 10, 20]

MODEL_IDS = {
    "e5": "intfloat/e5-small-v2",
    "sapbert": "cambridgeltl/SapBERT-from-PubMedBERT-fulltext",
}
QUERY_PREFIX = {"e5": "query: ", "sapbert": ""}


def load_stage3_names(path: Path) -> set[str]:
    """unmatched_for_stage3.csv → 대문자 name set (Stage 3 실제 입력)."""
    names = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            names.add(row["name"].strip().upper())
    return names


def load_corpus(path: Path) -> tuple[list[dict], dict]:
    """corpus jsonl 로드. 첫 줄 __meta__ 행은 분리해 반환."""
    rows = []
    meta = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            if obj.get("__meta__"):
                meta = obj
            else:
                rows.append(obj)
    return rows, meta


def load_dev(path: Path) -> dict[str, set[str]]:
    """raw_drug_name → {ingredient_norm, ...} (복합제는 여러 성분)"""
    result: dict[str, set[str]] = defaultdict(set)
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            result[row["raw_drug_name"]].add(row["ingredient_norm"].strip())
    return dict(result)


def recall_at_k(
    queries: list[str],
    gold: dict[str, set[str]],
    corpus: list[dict],
    index,
    model,
    prefix: str,
    k_max: int,
) -> dict[int, float]:
    import numpy as np

    vecs = model.encode(
        [prefix + q for q in queries],
        batch_size=256,
        show_progress_bar=True,
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype("float32")

    _, indices = index.search(vecs, k_max)  # (n_queries, k_max)

    hits = {k: 0 for k in KS}
    for i, qname in enumerate(queries):
        expected = gold[qname]  # set of ingredient_norm
        top_isets = [corpus[idx]["ingredient_set"] for idx in indices[i]]

        for k in KS:
            candidate_ingrs: set[str] = set()
            for iset in top_isets[:k]:
                for part in iset.split("|"):
                    candidate_ingrs.add(part.strip().upper())
            if expected.issubset(candidate_ingrs):
                hits[k] += 1

    n = len(queries)
    return {k: hits[k] / n for k in KS}


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 3-A Recall@K 측정")
    ap.add_argument("--index", type=Path, required=True, help="FAISS 인덱스 경로")
    ap.add_argument("--corpus", type=Path, required=True, help="alias_corpus.jsonl 경로")
    ap.add_argument("--dev", type=Path, default=DEV_DEFAULT, help="diana_golden_dev.csv")
    ap.add_argument("--model", choices=list(MODEL_IDS), default="e5")
    ap.add_argument("--out-csv", type=Path, help="결과 CSV 저장 경로 (선택)")
    ap.add_argument("--unmatched", type=Path, default=None,
                    help="unmatched_for_stage3.csv — 이 파일의 name과 겹치는 dev 쿼리만 평가 "
                         "(§9 leakage 방지: Stage 1/2가 이미 처리한 쿼리 제외)")
    args = ap.parse_args()

    for p in (args.index, args.corpus, args.dev):
        if not p.exists():
            sys.exit(f"오류: {p} 없음")

    import faiss
    from sentence_transformers import SentenceTransformer

    print(f"모델 로드: {MODEL_IDS[args.model]}", flush=True)
    model = SentenceTransformer(MODEL_IDS[args.model])

    print("인덱스·corpus 로드...", flush=True)
    index = faiss.read_index(str(args.index))
    corpus, corpus_meta = load_corpus(args.corpus)
    gold = load_dev(args.dev)

    # §7: corpus 버전 메타 출력 — retrieval_version이 Langfuse trace와 대응
    if corpus_meta:
        print(f"corpus 메타: dictionary_version={corpus_meta.get('dictionary_version')} "
              f"retrieval_version={corpus_meta.get('retrieval_version')} "
              f"alias_count={corpus_meta.get('alias_count'):,}", flush=True)

    queries = list(gold.keys())

    # §9 leakage 방지: --unmatched 지정 시 Stage 3 실제 입력과 겹치는 쿼리만 평가
    if args.unmatched:
        if not args.unmatched.exists():
            sys.exit(f"오류: {args.unmatched} 없음")
        stage3_names = load_stage3_names(args.unmatched)
        filtered = [q for q in queries if q.upper() in stage3_names]
        print(f"쿼리 필터링 (--unmatched): {len(queries):,} → {len(filtered):,}개 "
              f"(Stage 3 실제 입력 기준)", flush=True)
        queries = filtered
        gold = {q: gold[q] for q in queries}
    else:
        print(f"쿼리 {len(queries):,}개 (dev set 전체 — leakage 포함 가능, §9 참조)", flush=True)

    if not queries:
        sys.exit("평가 대상 쿼리 없음 (--unmatched와 dev set이 겹치지 않음)")

    scores = recall_at_k(queries, gold, corpus, index, model, QUERY_PREFIX[args.model], max(KS))

    print(f"\nRecall@K ({args.model}):")
    for k in KS:
        print(f"  K={k:<3}: {scores[k]:.4f}  ({int(scores[k]*len(queries))}/{len(queries)})")

    if args.out_csv:
        args.out_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["model", "k", "recall", "hits", "total"])
            for k in KS:
                w.writerow([args.model, k, f"{scores[k]:.4f}",
                             int(scores[k] * len(queries)), len(queries)])
        print(f"\n결과 → {args.out_csv}")


if __name__ == "__main__":
    main()
