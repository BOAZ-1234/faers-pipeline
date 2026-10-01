# 잔여 갭 조사 1단계 (primaryid "존재"가 아니라 "행 개수"까지 대조).
#
# 지난 anti-join은 "이 primaryid가 라이브에 있냐 없냐"만 봤다. 근데 신고서(primaryid)는
# 라이브에 있어도 그 신고서에 딸린 DRUG/REAC 행 일부만 청크 단위로 유실됐을 수 있다
# (원래 로더가 CHUNK_SIZE=30000 단위로 쓰는데, 크래시가 청크 경계에서 나면 신고서 자체는
# 이미 다른 청크에서 일부 적재된 채 남는다). 그래서 이번엔 primaryid별 "행 개수"를 로컬 zip
# 기준으로 다시 뽑는다 — S3는 안 건드리고, 이것도 100% 로컬 파일 처리.

import sys
import os
import glob
import zipfile
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pyarrow as pa
import pyarrow.parquet as pq

ZIP_GLOB = os.path.join(os.path.dirname(__file__), "..", "data", "faers_load", "faers_*.zip")
OUT_DIR = os.path.join(os.path.dirname(__file__), "row_counts")
os.makedirs(OUT_DIR, exist_ok=True)


def count_rows_per_primaryid(zip_path, file_type):
    with zipfile.ZipFile(zip_path, "r") as z:
        target_file = [f for f in z.namelist() if file_type in f.upper() and f.upper().endswith(".TXT")]
        if not target_file:
            return None
        target = target_file[0]
        with z.open(target) as f:
            header = f.readline().decode("utf-8").strip().split("$")
            header_lower = [h.replace("﻿", "").strip().lower() for h in header]
            if "primaryid" not in header_lower:
                return None
            pid_idx = header_lower.index("primaryid")

            counter = Counter()
            for raw_line in f:
                decoded = raw_line.decode("utf-8", errors="replace").strip()
                parts = decoded.split("$")
                if len(parts) != len(header):
                    continue
                counter[parts[pid_idx]] += 1
            return counter


def main():
    zip_paths = sorted(glob.glob(ZIP_GLOB))
    print(f"발견된 zip: {len(zip_paths)}개\n")

    for ft in ["DRUG", "REAC"]:
        source_zips, primaryids, counts = [], [], []
        for zip_path in zip_paths:
            file_name = os.path.basename(zip_path)
            counter = count_rows_per_primaryid(zip_path, ft)
            if counter is None:
                continue
            source_zips.extend([file_name] * len(counter))
            primaryids.extend(counter.keys())
            counts.extend(counter.values())
            print(f"  [{ft}] {file_name}: distinct primaryid {len(counter):,}건, 총 행 {sum(counter.values()):,}건")

        table = pa.table({"source_zip": source_zips, "primaryid": primaryids, "local_count": counts})
        out_path = os.path.join(OUT_DIR, f"{ft.lower()}_row_counts.parquet")
        pq.write_table(table, out_path)
        print(f"  -> 저장: {out_path} (총 {len(primaryids):,}행)\n")


if __name__ == "__main__":
    main()
