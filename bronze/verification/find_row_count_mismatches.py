# 잔여 갭 조사 2단계. extract_row_counts_from_zips.py가 뽑아둔 (source_zip, primaryid,
# local_count)와, 라이브 테이블의 primaryid별 실제 행 개수를 대조한다.
#
# 라이브 쪽 groupBy(primaryid).count()는 이제 faers_drug(19개 파일, ~1.28GB) /
# faers_reac(38개 파일, ~0.42GB) 전체를 한 번 훑는 작업이라 이미 확인된 소용량 — S3 조회
# 비용상 큰 스캔이 아니다(전체 8,400만 행짜리 원본 압축 전이면 부담됐겠지만 지금은 컴팩션
# 되어 있어서 파일 수가 적다).

import sys
import os
from dotenv import load_dotenv
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv("bronze/.env")
aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"

spark = SparkSession.builder \
    .appName("Find_Row_Count_Mismatches") \
    .config("spark.driver.memory", "8g") \
    .config("spark.sql.shuffle.partitions", "32") \
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

ROW_COUNTS_DIR = os.path.join(os.path.dirname(__file__), "row_counts")
TABLES = {"DRUG": "faers_drug", "REAC": "faers_reac"}

for ft, table in TABLES.items():
    print(f"\n{'='*70}\n{ft} ({table}) — primaryid별 행 개수 대조\n{'='*70}")

    local_df = spark.read.parquet(os.path.join(ROW_COUNTS_DIR, f"{ft.lower()}_row_counts.parquet"))
    live_counts = spark.table(f"my_catalog.stage_a_raw.{table}") \
        .groupBy("primaryid").count().withColumnRenamed("count", "live_count")

    joined = local_df.join(live_counts, on="primaryid", how="left") \
        .withColumn("live_count", F.coalesce(F.col("live_count"), F.lit(0))) \
        .withColumn("diff", F.col("local_count") - F.col("live_count"))

    mismatched = joined.filter("diff != 0")
    n_mismatched_primaryid = mismatched.count()
    total_missing_rows = mismatched.agg(F.sum("diff")).collect()[0][0] or 0
    print(f"행 개수가 안 맞는 primaryid: {n_mismatched_primaryid:,}건")
    print(f"그로 인한 총 누락 행 수(local-live 합): {total_missing_rows:,}")

    by_zip = mismatched.groupBy("source_zip") \
        .agg(F.count("*").alias("mismatched_primaryids"), F.sum("diff").alias("missing_rows")) \
        .orderBy(F.desc("missing_rows"))

    print("\n분기별 누락 행 수 (내림차순):")
    by_zip.show(60, truncate=False)

    out_csv = os.path.join(os.path.dirname(__file__), f"row_mismatch_{ft.lower()}_by_quarter.csv")
    by_zip.toPandas().to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"저장: {out_csv}")

    out_detail = os.path.join(os.path.dirname(__file__), f"row_mismatch_{ft.lower()}_detail.parquet")
    mismatched.write.mode("overwrite").parquet(out_detail)
    print(f"상세(primaryid별) 저장: {out_detail}")

spark.stop()
