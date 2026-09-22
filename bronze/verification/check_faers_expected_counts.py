# 적재 완결성 점검 (FAERS 2012Q4~2026Q2).
#
# 배경: load_faers_master.py는 source_zip 컬럼과 load_manifest(pipeline="faers") 기록을
# 남기도록 짜여 있지만, 실제 S3의 faers_demo/faers_drug/faers_reac 테이블에는 둘 다 없다.
# 즉 지금 라이브 데이터는 그 안전장치가 생기기 이전 버전(#10, 최초 로더)으로 적재된 것이고,
# load_manifest가 비어 있는 상태에서 load_faers_master.py를 그대로 재실행하면
# DEMO는 MERGE라 안전하지만 DRUG/REAC는 append라 전량이 중복 적재된다.
#
# 그래서 S3는 전혀 건드리지 않고, 로컬에 남아 있는 원본 zip(bronze/data/faers_load/*.zip,
# 54개)을 load_faers_master.py의 classify_line()과 완전히 동일한 로직으로 다시 파싱해서
# "각 분기 zip이 실제로 몇 건을 담고 있어야 하는가"를 구한다. 이건 순수 로컬 파일 읽기라
# S3 조회 비용이 전혀 들지 않는다.
#
# 이 스크립트가 하는 일은 딱 여기까지 — S3 라이브 테이블과의 최종 대조(라이브 카운트 대비
# 얼마나 비는가)는 이 결과를 별도로 이미 확인한 라이브 스냅샷 수치와 사람이 비교한다.
# (라이브 테이블에 source_zip이 없어서 분기 단위 자동 대조 자체가 불가능하기 때문)

import sys
import os
import glob
import zipfile
import json
import csv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# load_faers_master.py와 동일한 매핑/글롭 대상.
# 단, 실제 로더는 data/faers_not_load/faers_*.zip을 봤지만 지금 로컬엔 data/faers_load/에 있음.
FILE_TYPES = ["DEMO", "DRUG", "REAC"]
ZIP_GLOB = os.path.join(os.path.dirname(__file__), "..", "data", "faers_load", "faers_*.zip")


def classify_line(raw_line, header):
    """load_faers_master.py의 classify_line()과 동일한 판정 로직."""
    decoded_line = raw_line.decode("utf-8", errors="replace")
    data = decoded_line.strip().split("$")
    return len(data) == len(header)


def count_zip_file(zip_path, file_type):
    with zipfile.ZipFile(zip_path, "r") as z:
        file_list = z.namelist()
        target_file = [f for f in file_list if file_type in f.upper() and f.upper().endswith(".TXT")]
        if not target_file:
            return None  # 이 zip 안에 해당 file_type 파일이 없음
        target = target_file[0]
        with z.open(target) as f:
            header = f.readline().decode("utf-8").strip().split("$")
            ok_count = 0
            reject_count = 0
            for raw_line in f:
                if classify_line(raw_line, header):
                    ok_count += 1
                else:
                    reject_count += 1
            return {"ok": ok_count, "rejected": reject_count, "source_txt": target}


def main():
    zip_paths = sorted(glob.glob(ZIP_GLOB))
    print(f"발견된 zip 파일: {len(zip_paths)}개\n")

    results = []
    totals = {ft: {"ok": 0, "rejected": 0, "zips": 0} for ft in FILE_TYPES}

    for zip_path in zip_paths:
        file_name = os.path.basename(zip_path)
        row = {"source_zip": file_name}
        for ft in FILE_TYPES:
            res = count_zip_file(zip_path, ft)
            if res is None:
                row[f"{ft}_ok"] = None
                row[f"{ft}_rejected"] = None
                print(f"  ⚠️ [{file_name}] {ft} 파일 없음")
                continue
            row[f"{ft}_ok"] = res["ok"]
            row[f"{ft}_rejected"] = res["rejected"]
            totals[ft]["ok"] += res["ok"]
            totals[ft]["rejected"] += res["rejected"]
            totals[ft]["zips"] += 1
        results.append(row)
        print(f"  ✅ [{file_name}] DEMO={row.get('DEMO_ok')} DRUG={row.get('DRUG_ok')} REAC={row.get('REAC_ok')}")

    out_dir = os.path.dirname(__file__)
    csv_path = os.path.join(out_dir, "expected_counts_from_local_zips.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["source_zip"] + [f"{ft}_{k}" for ft in FILE_TYPES for k in ("ok", "rejected")]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in results:
            w.writerow(row)

    summary_path = os.path.join(out_dir, "expected_counts_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({"zip_count": len(zip_paths), "totals": totals}, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("로컬 zip 기준 기대 총합 (source-of-truth, S3 미조회)")
    print("=" * 60)
    for ft in FILE_TYPES:
        t = totals[ft]
        print(f"  {ft}: ok={t['ok']:,}  rejected={t['rejected']:,}  (zip {t['zips']}/{len(zip_paths)}개에서 발견)")
    print(f"\n결과 저장: {csv_path}")
    print(f"요약 저장: {summary_path}")


if __name__ == "__main__":
    main()
