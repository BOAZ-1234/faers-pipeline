"""
B세부1(B리드 겸임) — Stage1(사전+prod_ai)·Stage2(유사도)·BRAND_DOSE·OUT_OF_SCOPE·
Stage3(LLM)이 각자 다른 포맷으로 남긴 결과를 dbt 계약(stg_resolution/stg_candidate,
contracts/dbt/models/staging/schema.yml) 스키마 하나로 합친다.

왜 필요한가: 그 계약이 요구하는 RESOLUTION_FIELDS/CANDIDATE_FIELDS 모양으로 결과를
내는 건 stage3_harness.py 하나뿐이다(그 파일 자체가 계약 컬럼 그대로 설계됨). Stage1은
지금까지 "맞았다/틀렸다"(bool)만 보고 끝(커버리지 %만 계산), Stage2(similarity.py)·
BRAND_DOSE(renormalize.py)는 각자 다른 컬럼명으로만 남겨서 — 다섯 입력을 한 표로
합치는 지점이 없었다. 이 스크립트가 그 지점이다.

entity_id는 stage3_harness.py의 entity_id_for()를 그대로 재사용한다(새로 발명하지
않음) — 그래야 Stage3 산출물과 entity_id가 어긋나지 않는다. 같은 이유로, 서로 다른
raw 이름이 정규화 후 같은 entity_id로 묶이면(예: "ASPIRIN"과 "ASPIRIN NOS") 계약의
entity_id unique 제약(schema.yml: "약이름당 정확히 1행")을 지키기 위해 대표 1행만
남긴다 — 우선순위는 단계가 이를수록(Stage1 > Stage2 > BRAND_DOSE > Stage3 > OOS),
같은 단계면 신고건수(n_reports) 많은 쪽.

입력 중 없는 파일은 조용히 건너뛴다(예: Stage3 산출물을 아직 못 가져왔으면 그 부분만
빼고 나머지로 먼저 돌려볼 수 있음) — 콘솔에 몇 개를 건너뛰는지는 찍는다.

실행 예:
  python3 build_resolution.py \\
      --matched-csv out/stage1_matched.csv \\
      --similarity-csv similarity_matched.csv \\
      --brand-dose-csv out/brand_dose_resolved.csv \\
      --oos-csv out/stage3_oos.csv \\
      --stage3-dir prod_run3/final \\
      --out-resolution-csv out/resolution_merged.csv \\
      --out-candidate-csv out/candidate_merged.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from normalize import normalize_query
from build_map import DICTIONARY_VERSION
from stage3_harness import entity_id_for, RESOLUTION_FIELDS, CANDIDATE_FIELDS, PIPELINE_VERSION

# 단계 우선순위 — 숫자가 작을수록 entity_id 충돌 시 우선 채택(각주 참고).
PRIORITY = {"stage1": 0, "stage2": 1, "brand_dose": 2, "stage3": 3, "oos": 4}


def entity_id_of(name: str) -> str:
    key = normalize_query(name) or name.strip().upper()
    return entity_id_for(key)


def _read_csv(path: Path | None):
    if path is None:
        return []
    if not path.exists():
        print(f"  (건너뜀 — 없음: {path})")
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def from_stage1_matched(path: Path | None) -> list[dict]:
    """coverage.py --out-matched-csv 출력(name,n_reports,via,candidate_rank,ingredient_set,
    unii,source) → resolution 후보. candidate_rank가 2개 이상이면(사전 충돌, match.py
    참고) 보류로, 1개뿐이면 확정으로 본다."""
    rows = _read_csv(path)
    by_name: dict[str, list[dict]] = {}
    for r in rows:
        by_name.setdefault(r["name"], []).append(r)

    out = []
    for name, cands in by_name.items():
        n_reports = int(cands[0]["n_reports"] or 0)
        cands.sort(key=lambda c: int(c["candidate_rank"]))
        confirmed = len(cands) == 1
        top = cands[0]
        out.append({
            "raw_drug_name": name, "n_reports": n_reports, "stage": "stage1",
            "resolution_status": "확정" if confirmed else "보류",
            "resolved_ingredient_set": top["ingredient_set"] if confirmed else "",
            "unii": top["unii"] if confirmed else "",
            "resolved_stage": 1, "resolved_method": top["via"], "confidence": "",
            "candidates": cands if not confirmed else [],
        })
    return out


def from_similarity(path: Path | None) -> list[dict]:
    """similarity.py 기본 실행(--keep-ambiguous 없이) 결과는 이미 ambiguous=0인
    행만 남아 있다(run_match() 기본값 — 복합제라 애매하면 애초에 안 씀, 3단계로
    보류). 방어적으로 ambiguous=1인 행이 섞여 있으면(수동으로 --keep-ambiguous로
    돌린 파일을 잘못 넘긴 경우) 여기서도 보류로 내린다."""
    rows = _read_csv(path)
    out = []
    for r in rows:
        ambiguous = r.get("ambiguous", "0") not in ("0", "", None)
        out.append({
            "raw_drug_name": r["drugname_raw"], "n_reports": int(r["n_reports"] or 0),
            "stage": "stage2",
            "resolution_status": "보류" if ambiguous else "확정",
            "resolved_ingredient_set": "" if ambiguous else r["ingredient_set"],
            "unii": "" if ambiguous else r["unii"],
            "resolved_stage": 2, "resolved_method": "유사도",
            "confidence": r.get("confidence", ""),
            "candidates": [],
        })
    return out


def from_brand_dose(path: Path | None) -> list[dict]:
    """renormalize.py --out-resolved-csv 결과(name,n_reports,matched_key,rule,
    ingredient_set). UNII는 이 단계 산출물에 없다(브랜드+용량 재정규화는 사전
    재조회 결과의 ingredient_set만 돌려줌) — 빈값으로 둔다."""
    rows = _read_csv(path)
    out = []
    for r in rows:
        out.append({
            "raw_drug_name": r["name"], "n_reports": int(r["n_reports"] or 0),
            "stage": "brand_dose",
            "resolution_status": "확정",
            "resolved_ingredient_set": r["ingredient_set"], "unii": "",
            "resolved_stage": 1, "resolved_method": f"brand_dose:{r['rule']}",
            "confidence": "",
            "candidates": [],
        })
    return out


def from_oos(path: Path | None) -> list[dict]:
    """triage.py --out-oos-csv 결과(name,n_reports) — 성분 자체가 없는 비특정
    라벨(VITAMINS, UNSPECIFIED 등). resolved_ingredient_set 빈값으로 미해결
    포함한다(제외하지 않음 — 추적성 유지, B단계 GOLD는 빈 ingredient_set을
    자동으로 거른다, b_stage/schema.yml 참고)."""
    rows = _read_csv(path)
    out = []
    for r in rows:
        out.append({
            "raw_drug_name": r["name"], "n_reports": int(r["n_reports"] or 0),
            "stage": "oos",
            "resolution_status": "미해결",
            "resolved_ingredient_set": "", "unii": "",
            "resolved_stage": "", "resolved_method": "out_of_scope",
            "confidence": "",
            "candidates": [],
        })
    return out


def from_stage3(dirpath: Path | None) -> tuple[list[dict], list[dict]]:
    """stage3_harness.py finalize 결과(dest/resolution.csv, dest/candidate.csv) —
    이미 계약 컬럼 그대로라 거의 그대로 통과시킨다. entity_id는 파일에 있는 값을
    그대로 쓴다(같은 entity_id_for()로 만들어졌으니 재계산 불필요)."""
    if dirpath is None:
        return [], []
    res_path, cand_path = dirpath / "resolution.csv", dirpath / "candidate.csv"
    if not res_path.exists():
        print(f"  (건너뜀 — 없음: {res_path})")
        return [], []
    res_rows = list(csv.DictReader(open(res_path, newline="", encoding="utf-8")))
    cand_rows = list(csv.DictReader(open(cand_path, newline="", encoding="utf-8"))) if cand_path.exists() else []
    out = []
    for r in res_rows:
        out.append({
            "raw_drug_name": r["raw_drug_name"], "n_reports": 0, "stage": "stage3",
            "entity_id": r["entity_id"],  # 이미 계산된 값 — 재계산 안 함
            "resolution_status": r["resolution_status"],
            "resolved_ingredient_set": r["resolved_ingredient_set"], "unii": r["unii"],
            "resolved_stage": r["resolved_stage"], "resolved_method": r["resolved_method"],
            "confidence": r["confidence"],
            "candidates": [],
        })
    return out, cand_rows


def build(sources: dict[str, list[dict]]) -> tuple[list[dict], list[dict]]:
    """entity_id 기준으로 묶어 대표 1행만 남긴다(계약 unique 제약). 우선순위는
    PRIORITY, 동률이면 n_reports 많은 쪽."""
    pools: dict[str, list[dict]] = {}
    for stage_rows in sources.values():
        for row in stage_rows:
            eid = row.get("entity_id") or entity_id_of(row["raw_drug_name"])
            row["entity_id"] = eid
            pools.setdefault(eid, []).append(row)

    resolution_rows, candidate_rows = [], []
    collisions = 0
    for eid, rows in pools.items():
        if len(rows) > 1:
            collisions += 1
        rows.sort(key=lambda r: (PRIORITY[r["stage"]], -r["n_reports"]))
        rep = rows[0]
        resolution_rows.append({
            "entity_id": eid,
            "raw_drug_name": rep["raw_drug_name"],
            "resolution_status": rep["resolution_status"],
            "resolved_ingredient_set": rep["resolved_ingredient_set"],
            "unii": rep["unii"],
            "resolved_stage": rep["resolved_stage"],
            "resolved_method": rep["resolved_method"],
            "confidence": rep["confidence"],
            "pipeline_version": PIPELINE_VERSION,
            "dictionary_version": DICTIONARY_VERSION,
        })
        for rank, cand in enumerate(rep.get("candidates", []), start=1):
            candidate_rows.append({
                "entity_id": eid, "raw_drug_name": rep["raw_drug_name"],
                "normalized_drug_name": normalize_query(rep["raw_drug_name"]),
                "stage": rep["resolved_stage"] or "", "candidate_rank": rank,
                "candidate_alias": "", "candidate_ingredient_set": cand["ingredient_set"],
                "unii": cand["unii"], "lexical_score": "", "semantic_score": "",
                "source": cand.get("source", ""),
            })
    if collisions:
        print(f"  entity_id 충돌(서로 다른 raw 이름이 같은 entity로 묶임) {collisions:,}건 "
              f"— 우선순위로 대표 1행만 채택", flush=True)
    return resolution_rows, candidate_rows


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def main():
    ap = argparse.ArgumentParser(description="Stage1~3 결과를 stg_resolution/stg_candidate 계약 스키마로 병합")
    ap.add_argument("--matched-csv", type=Path, help="coverage.py --out-matched-csv 결과 (Stage1)")
    ap.add_argument("--similarity-csv", type=Path, help="similarity.py 결과 (Stage2, 기본 similarity_matched.csv)")
    ap.add_argument("--brand-dose-csv", type=Path, help="renormalize.py --out-resolved-csv 결과")
    ap.add_argument("--oos-csv", type=Path, help="triage.py --out-oos-csv 결과")
    ap.add_argument("--stage3-dir", type=Path, help="stage3_harness.py finalize --dest 디렉토리")
    ap.add_argument("--out-resolution-csv", type=Path, required=True)
    ap.add_argument("--out-candidate-csv", type=Path, required=True)
    args = ap.parse_args()

    print("Stage1(사전+prod_ai) 읽는 중...", flush=True)
    stage1 = from_stage1_matched(args.matched_csv)
    print(f"  {len(stage1):,}개")

    print("Stage2(유사도) 읽는 중...", flush=True)
    stage2 = from_similarity(args.similarity_csv)
    print(f"  {len(stage2):,}개")

    print("BRAND_DOSE 읽는 중...", flush=True)
    brand_dose = from_brand_dose(args.brand_dose_csv)
    print(f"  {len(brand_dose):,}개")

    print("OUT_OF_SCOPE 읽는 중...", flush=True)
    oos = from_oos(args.oos_csv)
    print(f"  {len(oos):,}개")

    print("Stage3(LLM) 읽는 중...", flush=True)
    stage3, stage3_candidates = from_stage3(args.stage3_dir)
    print(f"  {len(stage3):,}개 (candidate {len(stage3_candidates):,}개)")

    resolution_rows, candidate_rows = build({
        "stage1": stage1, "stage2": stage2, "brand_dose": brand_dose,
        "oos": oos, "stage3": stage3,
    })
    # Stage3 자체 candidate.csv는 이미 계약 컬럼 그대로라 그대로 합친다(위 build()가
    # stage3 쪽 candidates는 안 만들어서 — stage3_harness.py가 이미 따로 저장해뒀음).
    for r in stage3_candidates:
        candidate_rows.append({k: r.get(k, "") for k in CANDIDATE_FIELDS})

    write_csv(args.out_resolution_csv, RESOLUTION_FIELDS, resolution_rows)
    write_csv(args.out_candidate_csv, CANDIDATE_FIELDS, candidate_rows)

    print(f"\nstg_resolution 행 {len(resolution_rows):,}개 → {args.out_resolution_csv}")
    print(f"stg_candidate 행 {len(candidate_rows):,}개 → {args.out_candidate_csv}")

    status_counts: dict[str, int] = {}
    for r in resolution_rows:
        status_counts[r["resolution_status"]] = status_counts.get(r["resolution_status"], 0) + 1
    print("resolution_status 분포:", status_counts)


if __name__ == "__main__":
    main()
