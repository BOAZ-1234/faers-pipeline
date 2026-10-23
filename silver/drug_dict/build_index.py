"""
Stage 3-A — alias corpus 구축 + FAISS 인덱스 저장

drug_ingredient_map.csv에서 alias를 추출해 각 alias를 벡터화하고 FAISS 인덱스에 저장한다.
alias corpus는 jsonl로 함께 저장해 검색 결과 → ingredient_set 역참조에 쓴다.

alias 추출 전략:
  각 (medicinalproduct, ingredient_norm, ingredient_set) 행에서 다음을 alias로 쓴다:
  1. medicinalproduct 원본
  2. normalize_query(medicinalproduct) — 제형/용량 제거 버전
  3. ingredient_norm — 성분명도 alias (성분명으로 직접 검색하는 케이스 커버)
  4. ingredient_set 파이프 분해 → 각 성분 (복합제 성분도 개별 alias)

  중복 alias는 첫 번째 것 기준 ingredient_set을 채택 (사전 우선순위와 동일).

⚠️  corpus vs Stage 1 사전 모순 (캐스케이드 설계 §6-1):
  corpus는 Stage 1 사전(drug_ingredient_map)에서 파생된다.
  Stage 3까지 내려온 쿼리는 정의상 Stage 1 사전에 없던 표기다.
  따라서 corpus의 alias가 Stage 1 사전 key에 그대로 있었다면 Stage 1에서 이미 잡혔어야 한다 —
  Stage 3까지 내려왔다는 건 Stage 1 lookup 쪽 버그 신호일 수 있다.
  → Recall@K가 실제보다 부풀려 보일 수 있음. eval_recall.py 결과 해석 시 주의.
  → corpus는 "사전 표기 그대로" 복사가 아니라 "사전에 없던 변형까지 커버할 표준 표현"이
     이상적이나, 현재는 사전 전체를 alias로 쓰는 baseline 방식으로 시작한다.

재현성:
  corpus는 모델별 파일(alias_corpus_{model}.jsonl) — 각 FAISS 인덱스와 1:1로 짝.
  첫 줄은 __meta__ 행 — dictionary_version, embedding_model, retrieval_version.
  사전이 바뀌면(dictionary_version 변경) corpus와 인덱스를 다시 빌드해야 한다.

입력 경로:
  MAP_DEFAULT는 현재 팀 스냅샷이 올라와 있는 silver/reaction_dict/output/을 가리킨다.
  build_map.py의 생산 위치(silver/drug_dict/)와 다르므로, 다른 체크아웃에서 돌릴 땐
  --map으로 실제 drug_ingredient_map.csv 경로를 넘긴다.

실행:
  python3 build_index.py                    # E5-small (기본)
  python3 build_index.py --model sapbert    # SapBERT
  python3 build_index.py --model e5 --map <경로>/drug_ingredient_map.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from normalize import normalize_query  # Stage 0과 동일 정규화
from build_map import DICTIONARY_VERSION

MAP_DEFAULT = HERE / "drug_ingredient_map.csv"
OUT_DIR = HERE / "output"

MODEL_IDS = {
    "e5": "intfloat/e5-small-v2",
    "sapbert": "cambridgeltl/SapBERT-from-PubMedBERT-fulltext",
}

# E5는 쿼리와 passage에 prefix를 붙여야 최적 성능이 나온다 (intfloat 공식 권장).
# SapBERT는 prefix 없이 그대로.
QUERY_PREFIX = {"e5": "query: ", "sapbert": ""}
PASSAGE_PREFIX = {"e5": "passage: ", "sapbert": ""}

BATCH_SIZE = 512


def extract_aliases(map_path: Path) -> list[dict]:
    """drug_ingredient_map.csv → [{alias, ingredient_set}, ...]

    alias별 정답 ingredient_set을 보존한다. 같은 alias가 여러 행에 나오면
    먼저 등장한 행의 ingredient_set을 채택 (출처 우선순위 따름).

    복합제 보정: 성분명 별칭(ingr_norm 또는 iset 원소)이 복합제 묶음을 달고
    있으면 그 성분 하나로 바꾼다. 사전에서 별칭을 뽑을 때 복합제 행의 성분명
    AMLODIPINE에도 그 행의 묶음(amlodipine|hydrochlorothiazide|olmesartan)이
    붙는 문제(실측 130,963개 중 5,665개)를 build 시점에 바로잡는다. 브랜드
    별칭(SYMBICORT → budesonide|formoterol)은 묶음의 원소가 아니라 그대로 둔다."""
    from build_map import normalize_ingredient

    def collapse(alias: str, iset: str) -> str:
        parts = [p.strip() for p in iset.split("|") if p.strip()]
        if len(parts) < 2:
            return iset
        for a in (alias.strip().lower(), normalize_ingredient(alias).lower()):
            if a in parts:
                return a
        return iset

    seen: dict[str, str] = {}  # alias → ingredient_set (첫 등장 우선)
    rows = []

    with open(map_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            product = row["medicinalproduct"].strip()
            ingr_norm = row["ingredient_norm"].strip()
            ingr_set = row["ingredient_set"].strip()

            # ingredient_set이 여러 후보를 " ; "로 갖는 경우 첫 번째 채택
            iset = ingr_set.split(" ; ")[0].strip() if ingr_set else ingr_norm.lower()

            # alias 후보 — 순서 보존(list). set을 쓰면 프로세스마다 순회 순서가
            # 달라져(해시 랜덤화) corpus 행 ID가 빌드마다 바뀐다. E5/SapBERT를
            # 따로 빌드하면 인덱스 행 ID와 corpus 행이 어긋나 엉뚱한 성분을 반환.
            # 중복 제거는 아래 seen dict가 담당하므로 list로 충분하다.
            candidates = [
                product,
                normalize_query(product),
                ingr_norm,
            ]
            # ingredient_set 파이프 분해 → 각 성분도 alias
            for part in iset.split("|"):
                part = part.strip()
                if part:
                    candidates.append(part.upper())

            for alias in candidates:
                alias = alias.strip()
                if alias and alias not in seen:
                    seen[alias] = collapse(alias, iset)
                    rows.append({"alias": alias, "ingredient_set": seen[alias]})

    return rows


def embed(texts: list[str], model, prefix: str) -> "np.ndarray":
    import numpy as np
    prefixed = [prefix + t for t in texts]
    vecs = model.encode(
        prefixed,
        batch_size=BATCH_SIZE,
        show_progress_bar=True,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return vecs.astype("float32")


def build(map_path: Path, model_key: str, out_dir: Path) -> None:
    import faiss
    import numpy as np
    from sentence_transformers import SentenceTransformer

    print(f"모델 로드: {MODEL_IDS[model_key]}", flush=True)
    model = SentenceTransformer(MODEL_IDS[model_key])

    print("alias 추출 중...", flush=True)
    rows = extract_aliases(map_path)
    print(f"alias {len(rows):,}개", flush=True)

    aliases = [r["alias"] for r in rows]
    print("임베딩 중...", flush=True)
    vecs = embed(aliases, model, PASSAGE_PREFIX[model_key])

    dim = vecs.shape[1]
    index = faiss.IndexFlatIP(dim)  # 코사인(정규화 후 내적 = 코사인)
    index.add(vecs)

    out_dir.mkdir(parents=True, exist_ok=True)
    # corpus를 모델별 파일로 분리 — 인덱스 행 ID와 corpus 행이 항상 짝이 되게.
    # 공유 파일 1개면 두 번째 빌드가 덮어써서, 먼저 만든 인덱스가 바뀐 corpus를
    # 참조하는 사고가 난다(행 순서가 어긋나면 조용히 틀린 성분 반환).
    corpus_path = out_dir / f"alias_corpus_{model_key}.jsonl"
    index_path = out_dir / f"faiss_{model_key}.index"

    # §7 재현성: corpus 첫 줄은 __meta__ — Langfuse trace의 retrieval_version 등과 대응
    meta = {
        "__meta__": True,
        "dictionary_version": DICTIONARY_VERSION,   # build_map.py에서 import
        "embedding_model": MODEL_IDS[model_key],
        "retrieval_version": f"r-{model_key}-v1",
        "alias_count": len(rows),
    }
    # corpus는 model별 파일 — 각 인덱스가 자기 corpus를 짝으로 가진다
    with open(corpus_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    faiss.write_index(index, str(index_path))
    print(f"인덱스 → {index_path}  ({index.ntotal:,} 벡터, dim={dim})")
    print(f"corpus → {corpus_path}  ({len(rows):,}행, meta: {meta})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 3-A: alias corpus + FAISS 인덱스")
    ap.add_argument("--model", choices=list(MODEL_IDS), default="e5")
    ap.add_argument("--map", type=Path, default=MAP_DEFAULT,
                    help="drug_ingredient_map.csv 경로")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args()

    if not args.map.exists():
        sys.exit(f"오류: {args.map} 없음")

    # 파일의 dictionary_version이 현재 DICTIONARY_VERSION과 일치하는지 확인
    with open(args.map, newline="", encoding="utf-8") as _f:
        _row = next(csv.DictReader(_f), None)
    if _row and _row.get("dictionary_version") and _row["dictionary_version"] != DICTIONARY_VERSION:
        sys.exit(
            f"오류: 사전 버전 불일치 — 파일={_row['dictionary_version']}, "
            f"코드={DICTIONARY_VERSION}. 올바른 사전 파일을 사용하세요."
        )

    build(args.map, args.model, args.out_dir)


if __name__ == "__main__":
    main()
