# faers_drug / faers_reac(둘 다 append 전용)의 "가장 최근 커밋에 실제로 뭐가 들어갔는지"를
# Iceberg 증분(incremental) 읽기로 정확히 확인합니다. 임의의 LIMIT 10이 아니라, 진짜 마지막
# 커밋이 추가한 행만 골라서 봅니다. 그 행의 primaryid로 faers_demo의 fda_dt를 찾아
# 실제로 어느 분기 파일까지 처리됐는지 역추적합니다.

import sys
import os
from dotenv import load_dotenv
from pyspark.sql import SparkSession

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"

print("⏳ Spark 세션을 시작합니다... (마지막 커밋 내용 확인 모드)")
spark = SparkSession.builder \
    .appName("Check_Last_Commit_Content") \
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

NAMESPACE = "stage_a_raw"


def last_commit_primaryids(table_name, sample_size=10):
    full_name = f"my_catalog.{NAMESPACE}.{table_name}"

    snaps = spark.sql(f"""
        SELECT snapshot_id, committed_at
        FROM {full_name}.snapshots
        ORDER BY committed_at DESC
        LIMIT 2
    """).collect()

    if len(snaps) < 2:
        print(f"   ⚠️ {full_name}: 스냅샷이 2개 미만이라 증분 비교 불가")
        return None

    last_id, prev_id = snaps[0]["snapshot_id"], snaps[1]["snapshot_id"]
    print(f"\n📊 {full_name} — 마지막 커밋: {snaps[0]['committed_at']} (snapshot {last_id})")

    incremental = spark.read.format("iceberg") \
        .option("start-snapshot-id", prev_id) \
        .option("end-snapshot-id", last_id) \
        .load(full_name)

    ids = [r["primaryid"] for r in incremental.select("primaryid").distinct().limit(sample_size).collect()]
    print(f"   마지막 커밋에서 뽑은 primaryid 샘플: {ids}")
    return ids


faers_ids = set()
for t in ["faers_drug", "faers_reac"]:
    ids = last_commit_primaryids(t)
    if ids:
        faers_ids.update(ids)

if faers_ids:
    id_list = ",".join(f"'{i}'" for i in list(faers_ids)[:10])
    print(f"\n📅 위 primaryid들의 faers_demo상 fda_dt (= 실제 어느 분기 파일이었는지):")
    spark.sql(f"""
        SELECT primaryid, caseid, fda_dt, init_fda_dt
        FROM my_catalog.{NAMESPACE}.faers_demo
        WHERE primaryid IN ({id_list})
        ORDER BY fda_dt
        LIMIT 10
    """).show(truncate=False)

print("\n✅ 확인 완료.")
