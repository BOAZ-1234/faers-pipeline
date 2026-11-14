"""
B세부1(B리드 겸임) — faers_drug.drugname 원본 표기 전체를 entity_id로 매핑.

왜 필요한가: build_resolution.py가 만든 stg_resolution.raw_drug_name은 "대표" 1개
표기일 뿐이다 — 같은 entity_id로 묶인 다른 표기(예: "ASPIRIN NOS"가 "ASPIRIN"과
같은 entity면 "ASPIRIN NOS"라는 문자열 자체는 stg_resolution에 없음, build_resolution.py
build() 참고). 그래서 B단계 fct_case_drug_reaction이 faers_drug.drugname 원본 그대로
stg_resolution과 텍스트로 직접 조인하면, 대표로 안 뽑힌 표기들이 전부 매칭 실패한다.

그 대신 coverage.py --out-freq-csv로 이미 뽑아둔 전체 641,097개 고유 표기
(out/all_drugnames.csv) 각각에 entity_id_for(normalize_query(name))를 적용해
"원본 표기 → entity_id" 전체 매핑을 따로 만든다. B단계 dbt 모델은 이 매핑을 거쳐
entity_id로 stg_resolution과 조인하면 모든 원본 표기가 올바른 ingredient_set을
찾는다. entity_id 계산은 stage3_harness.py의 entity_id_for()를 그대로 재사용한다
(build_resolution.py와 동일 원칙 — 새로 발명하지 않음).

실행:
  python3 build_raw_entity_map.py out/all_drugnames.csv --out raw_name_entity_map.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from normalize import normalize_query
from stage3_harness import entity_id_for


def entity_id_of(name: str) -> str:
    key = normalize_query(name) or name.strip().upper()
    return entity_id_for(key)


def main():
    ap = argparse.ArgumentParser(description="원본 drugname 전체 → entity_id 매핑 생성")
    ap.add_argument("freq_csv", type=Path, help="coverage.py --out-freq-csv 결과 (name,n_reports)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    with open(args.freq_csv, newline="", encoding="utf-8") as f:
        names = [r["name"] for r in csv.DictReader(f)]
    print(f"{len(names):,}개 원본 표기 로드", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["raw_drug_name", "entity_id"])
        for name in names:
            w.writerow([name, entity_id_of(name)])
    print(f"매핑 저장 → {args.out} ({len(names):,}행)")


if __name__ == "__main__":
    main()
