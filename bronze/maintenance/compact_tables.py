# 지금까지 청크(3만 건)마다 파일이 하나씩 쌓여서 faers_demo는 스냅샷 879개,
# faers_drug는 파일 13,584개까지 잘게 쪼개졌습니다. 이것 때문에 MERGE/조회가 점점
# 느려지고, 지난번 실제 크래시(zstd 메모리 부족)의 근본 원인이기도 했습니다.
#
# 이 스크립트는 두 가지를 합니다:
#   1. faers_drug/faers_reac 중복 제거 — pipeline_common(멱등성) 적용 전에
#      크래시+재시작을 겪으면서 생겼을 수 있는, 완전히 똑같은 행(전 컬럼 일치)만
#      SELECT DISTINCT로 제거합니다. faers_demo는 MERGE라 애초에 중복이 없고,
#      aers_*는 처음부터 멱등성 코드로 돌아서 이 문제 자체가 없습니다.
#   2. rewrite_data_files/rewrite_manifests로 작은 파일들을 큰 파일로 합쳐서
#      성능 문제를 고칩니다.
# 둘 다 데이터의 "논리적 내용"은 안 바꾸고(중복 제거는 이미 사고로 생긴 걸
# 되돌리는 것), 스냅샷 히스토리도 안 건드립니다.
#
# ⚠️ 일부러 expire_snapshots는 안 넣었습니다. 기획서(4-6, D단계 MLflow 기록)가
#    "적재 시작 이후 시점은 Iceberg 타임트래블로 진짜 스냅샷을 보장한다"와
#    "snapshot_id를 MLflow에 남겨 재현성을 증명한다"를 명시적 설계 요건으로
#    걸어뒀기 때문에, 스냅샷을 지우면 이 요건이 깨집니다. 스냅샷 정리가 정말
#    필요해지면 프로젝트 전체 기간(6개월)을 보수적으로 커버하는 보존 기간으로
#    별도 검토 후 넣을 것 — 지금은 절대 자동으로 하지 않습니다.
#
# ⚠️ 실제 S3 데이터 파일을 읽고 다시 쓰는 무거운 작업입니다 (수천만 건 재작성).
#    반드시 모든 적재(FAERS+AERS 백필)가 끝난 뒤, 다른 쓰기 작업이 없을 때 실행하세요.
#    bronze/CLAUDE.md 규칙상 이런 대규모 작업은 사용자 확인 없이 자동 실행하지 않습니다.

import sys
import os
from dotenv import load_dotenv
from pyspark.sql import SparkSession

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"
NAMESPACE = "stage_a_raw"

TABLES = [
    "faers_demo", "faers_drug", "faers_reac", "faers_load_rejects",
    "aers_demo", "aers_drug", "aers_reac", "aers_load_rejects",
    "load_manifest",
]

# 멱등성 코드(pipeline_common) 적용 전에 크래시+재시작을 겪은 append 전용 테이블.
# faers_demo는 MERGE라 애초에 중복이 안 생기고, aers_*는 처음부터 멱등성 코드로
# 시작해서 이 문제가 없다 — 그래서 이 둘만 대상이다.
DEDUPE_CANDIDATES = ["faers_drug", "faers_reac"]


def dedupe(spark, table):
    """완전히 똑같은 행(전 컬럼 일치)만 제거한다 - 진짜 중복 신고가 아니라
    같은 파일을 두 번 먹여서 생긴 사고성 중복만 걸러내는 것이라 안전하다."""
    full_name = f"my_catalog.{NAMESPACE}.{table}"
    if not spark.catalog.tableExists(full_name):
        print(f"⚠️ {full_name}: 테이블 없음, 건너뜀")
        return

    before = spark.table(full_name).count()
    print(f"\n{'=' * 70}\n🧹 {full_name} 중복 제거 시작 (현재 {before}건)\n{'=' * 70}")

    spark.sql(f"""
        CREATE OR REPLACE TABLE {full_name} USING iceberg AS
        SELECT DISTINCT * FROM {full_name}
    """)

    after = spark.table(full_name).count()
    removed = before - after
    print(f"  ✅ {full_name}: {removed}건 중복 제거됨 ({before} -> {after})")


def compact(spark, table):
    full_name = f"my_catalog.{NAMESPACE}.{table}"
    if not spark.catalog.tableExists(full_name):
        print(f"⚠️ {full_name}: 테이블 없음, 건너뜀")
        return

    print(f"\n{'=' * 70}\n🗜️  {full_name} 컴팩션 시작\n{'=' * 70}")

    print("  1/2 rewrite_data_files (작은 파일들을 큰 파일로 합치는 중...)")
    spark.sql(f"CALL my_catalog.system.rewrite_data_files(table => '{NAMESPACE}.{table}')").show(truncate=False)

    print("  2/2 rewrite_manifests (매니페스트 파일 정리 중...)")
    spark.sql(f"CALL my_catalog.system.rewrite_manifests(table => '{NAMESPACE}.{table}')").show(truncate=False)

    print(f"  ✅ {full_name} 컴팩션 완료 (스냅샷 히스토리는 그대로 보존됨)")


def main():
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

    print("⏳ Spark 세션을 시작합니다... (컴팩션 모드)")
    spark = SparkSession.builder \
        .appName("Bronze_Table_Compaction") \
        .master("local[4]") \
        .config("spark.driver.memory", "6g") \
        .config("spark.sql.shuffle.partitions", "8") \
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

    for table in DEDUPE_CANDIDATES:
        dedupe(spark, table)

    for table in TABLES:
        compact(spark, table)

    print("\n🎉 전체 중복 제거 + 컴팩션 완료.")


if __name__ == "__main__":
    main()
