# 1단계: 로컬 zip 55개에서 DEMO/DRUG/REAC 각각의 primaryid를 (source_zip, file_type)별로
# distinct 추출해서 로컬 parquet으로 저장한다. S3는 전혀 안 건드림.
#
# DRUG/REAC는 primaryid당 여러 행(약물/반응 여러 개)이라 distinct로 압축해두면
# 다음 단계(find_missing_quarters.py)에서 라이브 테이블과 anti-join할 때 가벼워진다.
# "행 개수가 정확히 몇 개 비었나"가 아니라 "어느 분기의 어느 신고서가 라이브에 아예 없나"를
# 찾는 게 목적이라 이 정도 해상도(신고서 단위)면 충분하다.

import sys
import os
import glob
import zipfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pyarrow as pa
import pyarrow.parquet as pq

FILE_TYPES = ["DEMO", "DRUG", "REAC"]
ZIP_GLOB = os.path.join(os.path.dirname(__file__), "..", "data", "faers_load", "faers_*.zip")
OUT_DIR = os.path.join(os.path.dirname(__file__), "primaryids")
os.makedirs(OUT_DIR, exist_ok=True)


def extract_primaryids(zip_path, file_type):
    file_name = os.path.basename(zip_path)
    with zipfile.ZipFile(zip_path, "r") as z:
        target_file = [f for f in z.namelist() if file_type in f.upper() and f.upper().endswith(".TXT")]
        if not target_file:
            return None
        target = target_file[0]
        with z.open(target) as f:
            header = f.readline().decode("utf-8").strip().split("$")
            # load_faers_master.py의 harmonize_legacy_schema에서 컬럼명을 lower() 정규화하므로 동일하게 맞춘다
            header_lower = [h.replace("﻿", "").strip().lower() for h in header]
            if "primaryid" not in header_lower:
                print(f"    ⚠️ [{file_name}] {file_type}: primaryid 컬럼이 없음 (헤더: {header_lower[:5]}...)")
                return None
            pid_idx = header_lower.index("primaryid")

            pids = set()
            for raw_line in f:
                decoded = raw_line.decode("utf-8", errors="replace").strip()
                parts = decoded.split("$")
                if len(parts) != len(header):
                    continue  # classify_line 기준으로도 격리됐을 라인 — 애초에 카운트 대상 아님
                pids.add(parts[pid_idx])
            return pids


def main():
    zip_paths = sorted(glob.glob(ZIP_GLOB))
    print(f"발견된 zip: {len(zip_paths)}개\n")

    for ft in FILE_TYPES:
        rows_source_zip = []
        rows_primaryid = []
        for zip_path in zip_paths:
            file_name = os.path.basename(zip_path)
            pids = extract_primaryids(zip_path, ft)
            if pids is None:
                continue
            rows_source_zip.extend([file_name] * len(pids))
            rows_primaryid.extend(pids)
            print(f"  [{ft}] {file_name}: distinct primaryid {len(pids):,}건")

        table = pa.table({"source_zip": rows_source_zip, "primaryid": rows_primaryid})
        out_path = os.path.join(OUT_DIR, f"{ft.lower()}_primaryids.parquet")
        pq.write_table(table, out_path)
        print(f"  -> 저장: {out_path} (총 {len(rows_primaryid):,}행)\n")


if __name__ == "__main__":
    main()
