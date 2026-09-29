# 2단계: extract_primaryids_from_zips.py가 로컬에 뽑아둔 (source_zip, primaryid) 목록을
# 라이브 S3 Iceberg 테이블의 primaryid 컬럼(컬럼 프루닝 — 다른 컬럼은 안 읽음)과
# anti-join해서 "라이브에 아예 없는 primaryid가 어느 zip 소속인지"를 분기별로 집계한다.
#
# 비용 관점: faers_demo/drug/reac는 이미 컴팩션되어 각각 1/3/1개 파일(총 ~2.1GB)뿐이라
# primaryid 컬럼 하나만 읽어도 사실상 그 파일들을 한 번 읽는 것과 같다 — 이미 확인된 소용량.

import sys
import os
from dotenv import load_dotenv
from pyspark.sql import SparkSession

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv("bronze/.env")
aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"

spark = SparkSession.builder \
    .appName("Find_Missing_Quarters") \
    .config("spark.driver.memory", "6g") \
    .config("spark.sql.shuffle.partitions", "16") \
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

PRIMARYID_DIR = os.path.join(os.path.dirname(__file__), "primaryids")
TABLES = {"DEMO": "faers_demo", "DRUG": "faers_drug", "REAC": "faers_reac"}

for ft, table in TABLES.items():
    local_path = os.path.join(PRIMARYID_DIR, f"{ft.lower()}_primaryids.parquet")
    print(f"\n{'='*70}\n{ft} ({table})\n{'='*70}")

    local_df = spark.read.parquet(local_path)
    live_pids = spark.table(f"my_catalog.stage_a_raw.{table}").select("primaryid").distinct()

    missing = local_df.join(live_pids, on="primaryid", how="left_anti")
    missing_by_zip = missing.groupBy("source_zip").count().orderBy("source_zip")

    total_local = local_df.count()
    total_missing = missing.count()
    print(f"로컬 zip 기준 distinct primaryid: {total_local:,}")
    print(f"라이브에 없는 primaryid: {total_missing:,} ({total_missing/total_local*100:.2f}%)")
    print("\n분기별 누락 건수 (0건인 분기는 생략):")
    missing_by_zip.filter("count > 0").show(100, truncate=False)

    out_csv = os.path.join(os.path.dirname(__file__), f"missing_{ft.lower()}_by_quarter.csv")
    missing_by_zip.filter("count > 0").toPandas().to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"저장: {out_csv}")

spark.stop()
