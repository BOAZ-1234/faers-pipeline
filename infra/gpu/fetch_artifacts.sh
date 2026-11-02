#!/usr/bin/env bash
# 3단계 실행에 필요한 산출물을 레포 안 정해진 위치에 내려받는다(전부 gitignore). 이미 있으면 건너뛴다.
#   레포 루트에서:  bash infra/gpu/fetch_artifacts.sh
#
#   silver/drug_dict/drug_ingredient_map.csv          ← Release dict-*            (팀 합의 경로, #55)
#   silver/drug_dict/unmatched_for_stage3.csv         ← Release unmatched-for-stage3-*
#   silver/drug_dict/output/faiss_sapbert.index       ← Release faiss-sapbert-*   (#47)
#   silver/drug_dict/output/alias_corpus_sapbert.jsonl
#   silver/drug_dict/golden/output/diana_golden_dev.csv ← diana_golden.py (OSF에서 DiAna 다운로드)
#
# 새 스냅샷 Release가 올라오면 아래 태그만 바꾼다. 사전 버전이 코드와 다르면 하네스가 실행 시 멈춘다(#55 check_map).
set -euo pipefail

DICT_TAG=dict-2026-09-22
UNMATCHED_TAG=unmatched-for-stage3-2026-10-01
FAISS_TAG=faiss-sapbert-2026-10-14

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
D="$REPO_ROOT/silver/drug_dict"
BASE=https://github.com/BOAZ-1234/faers-pipeline/releases/download   # 공개 레포라 인증 불필요

get() {  # get <태그> <파일> <저장 폴더>
  local tag=$1 file=$2 dest=$3
  mkdir -p "$dest"
  if [ -s "$dest/$file" ]; then
    echo "있음  $dest/$file"
    return
  fi
  echo "받는 중 $tag/$file"
  curl -fL --retry 3 -o "$dest/$file.part" "$BASE/$tag/$file"
  mv "$dest/$file.part" "$dest/$file"
}

get "$DICT_TAG"      drug_ingredient_map.csv     "$D"
get "$UNMATCHED_TAG" unmatched_for_stage3.csv    "$D"
get "$FAISS_TAG"     faiss_sapbert.index         "$D/output"
get "$FAISS_TAG"     alias_corpus_sapbert.jsonl  "$D/output"

# DiAna dev는 자동 생성하지 않는다 — diana_golden.py를 빈도 필터(--freq-csv) 없이 돌리면 팀이 쓰는 dev(13,652쌍,
# seed=42, 신고 ≥200건)와 다른 파일이 나와 프롬프트·모델 점수가 비교 불가해진다. 팀 공용 파일을 그대로 둔다.
if [ -s "$D/golden/output/diana_golden_dev.csv" ]; then
  echo "있음  $D/golden/output/diana_golden_dev.csv"
else
  echo "!! $D/golden/output/diana_golden_dev.csv 없음 — 팀 공용 dev 파일(함하경 생성본)을 받아 이 위치에 둘 것"
fi

ls -la "$D"/drug_ingredient_map.csv "$D"/unmatched_for_stage3.csv "$D"/output/ 2>/dev/null || true
