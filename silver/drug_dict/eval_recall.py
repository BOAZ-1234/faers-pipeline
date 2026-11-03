"""
Stage 3-A — Recall@K 측정

build_index.py로 만든 FAISS 인덱스에 diana_golden_dev.csv의 raw_drug_name을 쿼리해
Recall@1/5/10/20 곡선을 출력한다. E5 vs SapBERT 비교 + fuzzy 단독·union 측정에 쓴다.

평가 방식:
  쿼리: raw_drug_name (정규화 없음 — Stage 0 normalize_query는 캐스케이드가 적용할 것이고
  여기선 "첫 접촉" 시나리오를 측정하는 것이라 원본 그대로 넣는다)
  정답: ingredient_norm이 Top-K 후보 중 어느 ingredient_set에 포함되면 hit

  약물명 단위로 계산 (같은 raw_drug_name의 성분이 여러 개인 복합제는
  모든 성분이 Top-K 안에 있어야 hit — AND 기준).

실행:
  # embedding 단독
  python3 eval_recall.py --index output/faiss_sapbert.index --corpus output/alias_corpus_sapbert.jsonl --model sapbert
  # fuzzy 단독
  python3 eval_recall.py --fuzzy-only
  # fuzzy ∪ embedding (후보 합집합 recall)
  python3 eval_recall.py --index output/faiss_sapbert.index --corpus output/alias_corpus_sapbert.jsonl --model sapbert --union
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

UNMATCHED_DEFAULT = HERE / "unmatched_for_stage3.csv"
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
) -> tuple[dict[int, float], dict[int, int], dict[str, list[str]]]:
    """embedding Top-K 후보로 Recall@K 측정. 반환: scores, hits, query→top_isets."""
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
    top_by_query: dict[str, list[str]] = {}  # union 계산용 — K별 top ingredient_set 리스트
    for i, qname in enumerate(queries):
        expected = gold[qname]
        top_isets = [corpus[idx]["ingredient_set"] for idx in indices[i] if idx != -1]
        top_by_query[qname] = top_isets

        for k in KS:
            if _covers(top_isets[:k], expected):
                hits[k] += 1

    n = len(queries)
    return {k: hits[k] / n for k in KS}, hits, top_by_query


def _covers(isets: list[str], expected: set[str]) -> bool:
    """ingredient_set 리스트가 expected 성분을 모두 포함하면 True.
    gold_vocab 동의어 매핑으로 후보 쪽을 확장해 비교
    (예: 후보에 'ascorbic acid'면 VITAMIN C도 커버한 것으로 인정)."""
    from gold_vocab import expand_candidate_names

    candidate_ingrs: set[str] = set()
    for iset in isets:
        for part in iset.split("|"):
            candidate_ingrs.add(part.strip().upper())
    candidate_ingrs = expand_candidate_names(candidate_ingrs)
    return expected.issubset(candidate_ingrs)


def fuzzy_top_isets(queries: list[str], k_max: int) -> dict[str, list[str]]:
    """Stage 2 fuzzy의 상위 k_max개 후보에 붙은 ingredient_set을 그대로 돌려준다.
    채택 임계값·마진은 적용하지 않는다 — recall 측정은 "후보에 정답이 있나"지
    "자동 확정 가능한가"가 아니다 (Gate 3 — judge accuracy와 분리).

    복합제 보정: cands.canonical(cand)를 써서 similarity.py의 복합제 collapse를 재사용한다
    (PR#43에서 Stage 2 매칭에만 적용돼 있던 로직). records[cand][0]만 쓰면
    amlodipine alias가 복합제 묶음(amlodipine|hydrochlorothiazide|olmesartan) 전체로 세어져
    fuzzy·union recall을 부풀린다 (Codex 리뷰 P2)."""
    from rapidfuzz import fuzz
    from similarity import Candidates, block_key, load_candidates
    from normalize import normalize_query
    from rapidfuzz import process

    cands = load_candidates()
    out: dict[str, list[str]] = {}
    for name in queries:
        q = normalize_query(name)
        if not q:
            out[name] = []
            continue
        choices = cands.buckets.get(block_key(q)) or []
        if not choices:
            out[name] = []
            continue
        hits = process.extract(q, choices, scorer=fuzz.token_sort_ratio, limit=k_max)
        isets: list[str] = []
        for cand, _score, _idx in hits:
            rec, _ambiguous = cands.canonical(cand)  # 복합제 성분 alias → 성분 하나로 접힌 행
            iset = rec.get("ingredient_set", "").split(" ; ")[0].strip() \
                   or rec.get("ingredient_norm", "").lower()
            isets.append(iset)
        out[name] = isets
    return out


def union_recall(
    embed_top: dict[str, list[str]],
    fuzzy_top: dict[str, list[str]],
    gold: dict[str, set[str]],
) -> tuple[dict[int, float], dict[int, int]]:
    """embedding Top-K와 fuzzy Top-K 후보 합집합(각각 K개씩, 총 ≤2K)에서 recall 측정."""
    hits = {k: 0 for k in KS}
    for qname, expected in gold.items():
        for k in KS:
            merged = embed_top.get(qname, [])[:k] + fuzzy_top.get(qname, [])[:k]
            if _covers(merged, expected):
                hits[k] += 1
    n = len(gold)
    return {k: hits[k] / n for k in KS}, hits


def fuzzy_only_recall(
    queries: list[str],
    gold: dict[str, set[str]],
    k_max: int,
) -> tuple[dict[int, float], dict[int, int]]:
    """fuzzy 단독 Recall@K."""
    fuzzy_top = fuzzy_top_isets(queries, k_max)
    hits = {k: 0 for k in KS}
    for qname in queries:
        expected = gold[qname]
        for k in KS:
            if _covers(fuzzy_top.get(qname, [])[:k], expected):
                hits[k] += 1
    n = len(queries)
    return {k: hits[k] / n for k in KS}, hits


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 3-A Recall@K 측정 (embedding / fuzzy / union)")
    ap.add_argument("--index", type=Path, help="FAISS 인덱스 경로 (embedding 쓸 때)")
    ap.add_argument("--corpus", type=Path, help="alias_corpus.jsonl 경로 (embedding 쓸 때)")
    ap.add_argument("--dev", type=Path, default=DEV_DEFAULT, help="diana_golden_dev.csv")
    ap.add_argument("--model", choices=list(MODEL_IDS), default="sapbert")
    ap.add_argument("--fuzzy-only", action="store_true",
                    help="fuzzy 단독 recall만 측정 (--index, --corpus 불필요)")
    ap.add_argument("--union", action="store_true",
                    help="embedding Top-K ∪ fuzzy Top-K 합집합 recall도 측정 (embedding 모드와 함께)")
    ap.add_argument("--out-csv", type=Path, help="결과 CSV 저장 경로 (선택)")
    ap.add_argument("--unmatched", type=Path, default=UNMATCHED_DEFAULT,
                    help="unmatched_for_stage3.csv — 이 파일의 name과 겹치는 dev 쿼리만 평가 "
                         "(§9 leakage 방지: Stage 1/2가 이미 처리한 쿼리 제외)")
    ap.add_argument("--keep-oos", action="store_true",
                    help="OUT_OF_SCOPE 라벨(VITAMINS, UNSPECIFIED / SKIN CARE / PLACEBO 등)을 "
                         "유지. 기본은 제외 — Triage가 분리했어야 하는 라벨은 Stage 3 recall에 부당하게 반영됨")
    args = ap.parse_args()

    if not args.fuzzy_only:
        if not args.index or not args.corpus:
            sys.exit("오류: embedding 모드는 --index와 --corpus 필요 (또는 --fuzzy-only)")
        for p in (args.index, args.corpus):
            if not p.exists():
                sys.exit(f"오류: {p} 없음")
    if not args.dev.exists():
        sys.exit(f"오류: {args.dev} 없음")

    gold = load_dev(args.dev)
    queries = list(gold.keys())

    # §9 leakage 방지
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

    # OUT_OF_SCOPE 라벨 제외 (Triage가 분리했어야 하는 비성분 라벨)
    if not args.keep_oos:
        from gold_vocab import filter_out_of_scope_gold
        before = len(gold)
        gold, removed = filter_out_of_scope_gold(gold)
        queries = [q for q in queries if q in gold]
        print(f"OUT_OF_SCOPE 제거: {before:,} → {len(gold):,}개 "
              f"({removed}개 제외 — 전부 OOS 라벨만 가진 쿼리)")
        if not queries:
            sys.exit("평가 대상 쿼리 없음 (OOS 제외 후)")

    results: list[tuple[str, dict[int, float], dict[int, int]]] = []
    n = len(queries)

    # fuzzy 단독
    if args.fuzzy_only or args.union:
        print("\nfuzzy 단독 측정...", flush=True)
        fuzzy_scores, fuzzy_hits = fuzzy_only_recall(queries, gold, max(KS))
        results.append(("fuzzy", fuzzy_scores, fuzzy_hits))
        print(f"Recall@K (fuzzy):")
        for k in KS:
            print(f"  K={k:<3}: {fuzzy_scores[k]:.4f}  ({fuzzy_hits[k]}/{n})")

    # embedding
    embed_top = None
    if not args.fuzzy_only:
        import faiss
        from sentence_transformers import SentenceTransformer

        print(f"\n모델 로드: {MODEL_IDS[args.model]}", flush=True)
        model = SentenceTransformer(MODEL_IDS[args.model])

        print("인덱스·corpus 로드...", flush=True)
        index = faiss.read_index(str(args.index))
        corpus, corpus_meta = load_corpus(args.corpus)

        if corpus_meta:
            print(f"corpus 메타: dictionary_version={corpus_meta.get('dictionary_version')} "
                  f"retrieval_version={corpus_meta.get('retrieval_version')} "
                  f"alias_count={corpus_meta.get('alias_count'):,}", flush=True)

        scores, hits, embed_top = recall_at_k(
            queries, gold, corpus, index, model, QUERY_PREFIX[args.model], max(KS)
        )
        results.append((args.model, scores, hits))
        print(f"\nRecall@K ({args.model}):")
        for k in KS:
            print(f"  K={k:<3}: {scores[k]:.4f}  ({hits[k]}/{n})")

    # union
    if args.union and embed_top is not None:
        fuzzy_top = fuzzy_top_isets(queries, max(KS))
        union_scores, union_hits = union_recall(embed_top, fuzzy_top, gold)
        results.append((f"{args.model}∪fuzzy", union_scores, union_hits))
        print(f"\nRecall@K ({args.model} ∪ fuzzy):")
        for k in KS:
            print(f"  K={k:<3}: {union_scores[k]:.4f}  ({union_hits[k]}/{n})")

    if args.out_csv:
        args.out_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["method", "k", "recall", "hits", "total"])
            for method, scores, hits in results:
                for k in KS:
                    w.writerow([method, k, f"{scores[k]:.4f}", hits[k], n])
        print(f"\n결과 → {args.out_csv}")


if __name__ == "__main__":
    main()
