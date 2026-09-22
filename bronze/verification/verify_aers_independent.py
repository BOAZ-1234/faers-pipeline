# AERS(2004Q1~2012Q3) 독립 검증. load_manifest는 로더가 스스로 남긴 기록이라, FAERS 때처럼
# 원본 zip을 로더와 같은 파싱 로직(classify_line)으로 다시 세어 라이브 테이블과 직접 대조한다.
#
# 대조 3개:
#   local  : 로컬 zip을 다시 파싱한 정상 행 수 (zip x file_type)
#   live   : aers_* 테이블의 source_zip별 행 수 (컬럼 하나 집계 — 적은 파일이라 저렴)
#   manifest: 로더가 기록한 row_count
# DRUG/REAC는 append라 local == live 여야 한다. DEMO는 ISR 기준 MERGE라 zip 간 ISR 중복이 있으면
# 나중에 처리된 zip으로 source_zip이 옮겨가므로, DEMO는 전체 distinct ISR 합계로도 대조한다.

import sys
import os
import glob
import zipfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from load_aers_master import classify_line  # noqa: E402  (로더와 동일한 파싱 판정)

from dotenv import load_dotenv
from pyspark.sql import SparkSession

load_dotenv("bronze/.env")
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"
NAMESPACE = "stage_a_raw"
ZIP_GLOB = os.path.join(os.path.dirname(__file__), "..", "data", "aers_not_load", "aers_*.zip")
FILE_TYPES = ["DEMO", "DRUG", "REAC"]


def parse_local(zip_path, file_type):
    file_name = os.path.basename(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        target = [f for f in z.namelist() if file_type in f.upper() and f.upper().endswith(".TXT")]
        if not target:
            return None
        target = target[0]
        with z.open(target) as f:
            header = f.readline().decode("utf-8").strip().split("$")
            hl = [h.replace("﻿", "").strip().lower() for h in header]
            isr_idx = hl.index("isr") if "isr" in hl else None
            ok, rej, isrs = 0, 0, set()
            line_no = 1
            for raw in f:
                line_no += 1
                row, reject = classify_line(raw, header, file_name, target, file_type, line_no)
                if row is None:
                    rej += 1
                    continue
                ok += 1
                if file_type == "DEMO" and isr_idx is not None:
                    isrs.add(row[isr_idx])
            return {"ok": ok, "rej": rej, "isrs": isrs}


zip_paths = sorted(glob.glob(ZIP_GLOB))
print(f"로컬 AERS zip: {len(zip_paths)}개")

local = {}          # (zip, ft) -> ok
local_rej = {}
demo_isr_all = set()
demo_isr_per_zip = {}
for zp in zip_paths:
    name = os.path.basename(zp)
    for ft in FILE_TYPES:
        r = parse_local(zp, ft)
        if r is None:
            print(f"  ⚠️ {name} {ft} 파일 없음")
            continue
        local[(name, ft)] = r["ok"]
        local_rej[(name, ft)] = r["rej"]
        if ft == "DEMO":
            demo_isr_per_zip[name] = len(r["isrs"])
            demo_isr_all |= r["isrs"]
print("로컬 파싱 완료\n")

spark = SparkSession.builder \
    .appName("Verify_AERS_Independent") \
    .config("spark.driver.memory", "4g") \
    .config("spark.jars.packages", "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0,org.apache.hadoop:hadoop-aws:3.3.4,com.amazonaws:aws-java-sdk-bundle:1.12.262") \
    .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions") \
    .config("spark.sql.catalog.my_catalog", "org.apache.iceberg.spark.SparkCatalog") \
    .config("spark.sql.catalog.my_catalog.type", "hadoop") \
    .config("spark.sql.catalog.my_catalog.warehouse", f"s3a://{BUCKET_NAME}/iceberg_warehouse") \
    .config("spark.hadoop.fs.s3a.access.key", os.getenv("AWS_ACCESS_KEY_ID")) \
    .config("spark.hadoop.fs.s3a.secret.key", os.getenv("AWS_SECRET_ACCESS_KEY")) \
    .config("spark.hadoop.fs.s3a.endpoint", "s3.ap-northeast-2.amazonaws.com") \
    .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem") \
    .config("spark.hadoop.fs.s3a.aws.credentials.provider", "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider") \
    .config("spark.hadoop.fs.s3a.path.style.access", "true") \
    .getOrCreate()

manifest = {}
for r in spark.table(f"my_catalog.{NAMESPACE}.load_manifest").filter("pipeline = 'aers'").collect():
    manifest[(r["source_zip"], r["file_type"])] = (r["status"], r["row_count"], r["rejected_count"])

problems = 0
for ft in FILE_TYPES:
    tbl = f"my_catalog.{NAMESPACE}.aers_{ft.lower()}"
    live_rows = spark.table(tbl).groupBy("source_zip").count().collect()
    live = {r["source_zip"]: r["count"] for r in live_rows}
    total_local = sum(v for (z, f), v in local.items() if f == ft)
    total_live = sum(live.values())
    print(f"{'='*72}\n{ft}: 로컬 합계 {total_local:,} / 라이브 합계 {total_live:,} / 차이 {total_local-total_live:,}")

    bad_manifest = 0
    for zp in zip_paths:
        name = os.path.basename(zp)
        loc = local.get((name, ft))
        lv = live.get(name, 0)
        mf = manifest.get((name, ft))
        # 로더 기록(row_count)과 로컬 재파싱이 다르면: 로컬 zip이 로드에 쓰인 것과 다르다는 뜻
        if mf is None or mf[0] != "complete" or mf[1] != loc or (mf[2] or 0) != local_rej[(name, ft)]:
            bad_manifest += 1
            print(f"  [manifest 불일치] {name}: local ok={loc} rej={local_rej[(name, ft)]} manifest={mf}")
        if ft != "DEMO" and loc != lv:
            problems += 1
            print(f"  [라이브 불일치] {name}: local={loc:,} live={lv:,} diff={loc-lv:,}")
    if None in live or "" in live:
        print(f"  ⚠️ source_zip이 NULL인 라이브 행: {live.get(None, 0):,}")
    print(f"  manifest 불일치 zip 수: {bad_manifest}")

# DEMO는 ISR 기준 MERGE이므로 전체 distinct ISR과 대조
demo_live_total = spark.table(f"my_catalog.{NAMESPACE}.aers_demo").count()
demo_live_distinct = spark.table(f"my_catalog.{NAMESPACE}.aers_demo").select("isr").distinct().count()
print(f"{'='*72}\nDEMO(ISR 기준): 로컬 distinct ISR {len(demo_isr_all):,} / 라이브 행 {demo_live_total:,} / 라이브 distinct ISR {demo_live_distinct:,}")
if len(demo_isr_all) != demo_live_distinct:
    problems += 1
    print(f"  ⚠️ distinct ISR 불일치: diff={len(demo_isr_all)-demo_live_distinct:,}")
if demo_live_total != demo_live_distinct:
    problems += 1
    print(f"  ⚠️ 라이브 DEMO에 ISR 중복: {demo_live_total-demo_live_distinct:,}")

print(f"\n{'='*72}\n결론: 문제 항목 {problems}건")
spark.stop()
