# faers_reac이 2014Q2/2014Q1/2013Q4까지 정말 다 끝났는지, 데이터 파일은 전혀 안 읽고
# (1) 로컬 zip의 정확한 REAC 줄 수 합계와 (2) Iceberg 스냅샷 메타데이터의 added_records 합계만
# 비교해서 확인합니다. 데이터 파일 스캔이 없어서 사실상 비용이 들지 않습니다.

import sys
import os
import zipfile
from dotenv import load_dotenv
from pyspark.sql import SparkSession

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def count_reac_lines(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        reac_file = [f for f in z.namelist() if "REAC" in f.upper() and f.upper().endswith(".TXT")][0]
        with z.open(reac_file) as f:
            header = f.readline().decode("utf-8").strip().split("$")
            count = 0
            for line in f:
                data = line.decode("utf-8", errors="replace").strip().split("$")
                if len(data) == len(header):
                    count += 1
    return count


FILES = [
    "data/faers_not_load/faers_ascii_2014q2.zip",
    "data/faers_not_load/faers_ascii_2014q1.zip",
    "data/faers_not_load/faers_ascii_2013q4.zip",
]

expected_total = 0
for f in FILES:
    n = count_reac_lines(f)
    print(f"📦 {f}: REAC {n}건 (로컬, 무료)")
    expected_total += n
print(f"   합계(기대값): {expected_total}건\n")

load_dotenv()
aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"

print("⏳ Spark 세션을 시작합니다... (스냅샷 메타데이터만 조회, 데이터 파일 안 읽음)")
spark = SparkSession.builder \
    .appName("Check_Reac_Completeness") \
    .config("spark.jars.packages", "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0,org.apache.hadoop:hadoop-aws:3.3.4,com.amazonaws:aws-java-sdk-bundle:1.12.262") \
    .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions") \
    .config("spark.sql.catalog.my_catalog", "org.apache.iceberg.spark.SparkCatalog") \
    .config("spark.sql.catalog.my_catalog.type", "hadoop") \
    .config("spark.sql.catalog.my_catalog.warehouse", f"s3a://{BUCKET_NAME}/iceberg_warehouse") \
    .config("spark.hadoop.fs.s3a.access.key", aws_access_key) \
    .config("spark.hadoop.fs.s3a.secret.key", aws_secret_key) \
    .config("spark.hadoop.fs.s3a.endpoint", "s3.ap-northeast-2.amazonaws.com") \
    .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem") \
    .config("spark.hadoop.fs.s3a.aws.credentials.provider", "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider") \
    .config("spark.hadoop.fs.s3a.path.style.access", "true") \
    .getOrCreate()

snaps = spark.sql("""
    SELECT committed_at, summary['added-records'] AS added_records
    FROM my_catalog.stage_a_raw.faers_reac.snapshots
    WHERE committed_at >= TIMESTAMP '2026-09-03 00:00:00'
    ORDER BY committed_at
""").collect()

actual_total = sum(int(r["added_records"] or 0) for r in snaps)
print(f"\n📊 오늘(2026-09-03) faers_reac에 실제로 커밋된 스냅샷 {len(snaps)}개, 합계 {actual_total}건")
for r in snaps:
    print(f"   {r['committed_at']}  +{r['added_records']}")

print(f"\n{'=' * 60}")
print(f"기대값(로컬 3개 파일 REAC 합계): {expected_total}")
print(f"실제값(오늘 커밋된 스냅샷 합계):   {actual_total}")
if actual_total >= expected_total:
    print("✅ 2014Q2/2014Q1/2013Q4 REAC 전부 완료된 것으로 보입니다.")
else:
    print(f"⚠️ {expected_total - actual_total}건 부족 — 아직 덜 들어간 상태입니다 (아마 2013Q4 REAC 마지막 부분).")

print("\n✅ 확인 완료 (데이터 파일은 전혀 읽지 않았습니다).")
