# 지금 돌아가고 있는 load_faers_master.py / load_aers_master.py가 S3에 어디까지 적재됐는지
# 빠르게 확인하는 스크립트입니다. 데이터를 직접 스캔하지 않고 Iceberg 스냅샷 메타데이터만 조회해서
# (실제 데이터 파일은 안 건드림) S3 조회 비용을 최소화합니다. 각 테이블 최근 스냅샷 10개만 봅니다.

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

print("⏳ Spark 세션을 시작합니다... (진행상황 확인 모드)")
spark = SparkSession.builder \
    .appName("Check_Load_Progress") \
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
TABLES = [
    "faers_demo", "faers_drug", "faers_reac", "faers_load_rejects",
    "aers_demo", "aers_drug", "aers_reac", "aers_load_rejects",
]


def check_progress(table_name):
    full_name = f"my_catalog.{NAMESPACE}.{table_name}"
    print(f"\n{'=' * 70}\n📊 {full_name}\n{'=' * 70}")

    if not spark.catalog.tableExists(full_name):
        print("   ⚠️ 아직 테이블이 생성되지 않음 (이 테이블 적재 전이거나 아직 첫 청크 못 씀)")
        return

    # 데이터 파일은 안 읽고 스냅샷 메타데이터(커밋 시각 · 추가 레코드 수)만 조회 -> 사실상 비용 0
    snapshots = spark.sql(f"""
        SELECT committed_at, operation, summary['added-records'] AS added_records
        FROM {full_name}.snapshots
        ORDER BY committed_at DESC
        LIMIT 10
    """)
    snapshots.show(truncate=False)

    print("   👀 최근 커밋된 데이터 10건 미리보기:")
    spark.table(full_name).limit(10).show(truncate=False)


for t in TABLES:
    check_progress(t)

print("\n✅ 진행상황 확인 완료.")
