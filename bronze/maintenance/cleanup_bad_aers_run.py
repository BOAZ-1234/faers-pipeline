# classify_line 버그(AERS 행 끝의 종결 '$' 처리 안 함)로 인해 aers_* 전량이
# 격리 테이블로 잘못 들어간 첫 실행분을 정리합니다.
# - aers_demo/aers_drug/aers_reac: write_to_iceberg가 한 번도 안 불렸으므로 애초에
#   테이블 자체가 없다 (확인만 하고 별도 조치 없음).
# - load_manifest: pipeline='aers' 행을 전부 지운다 (전부 "complete(0건)"로
#   잘못 기록돼 있어서, 안 지우면 재실행 시 전부 건너뛰게 됨).
# - aers_load_rejects: 이번 버그로 생긴 쓰레기 데이터뿐이므로 테이블째 드롭한다.

import sys
import os
from dotenv import load_dotenv
from pyspark.sql import SparkSession

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"
NAMESPACE = "stage_a_raw"

spark = SparkSession.builder \
    .appName("Cleanup_Bad_AERS_Run") \
    .master("local[4]") \
    .config("spark.driver.memory", "6g") \
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

for t in ["aers_demo", "aers_drug", "aers_reac"]:
    full = f"my_catalog.{NAMESPACE}.{t}"
    exists = spark.catalog.tableExists(full)
    print(f"{full} 존재 여부: {exists}")
    assert not exists, f"{full}이 존재합니다 — 예상과 다르니 여기서 멈춥니다."

manifest = f"my_catalog.{NAMESPACE}.load_manifest"
before = spark.sql(f"SELECT COUNT(*) c FROM {manifest} WHERE pipeline = 'aers'").collect()[0]["c"]
spark.sql(f"DELETE FROM {manifest} WHERE pipeline = 'aers'")
after = spark.sql(f"SELECT COUNT(*) c FROM {manifest} WHERE pipeline = 'aers'").collect()[0]["c"]
print(f"load_manifest: pipeline='aers' 행 {before}건 삭제 -> 남은 {after}건")

rejects = f"my_catalog.{NAMESPACE}.aers_load_rejects"
if spark.catalog.tableExists(rejects):
    spark.sql(f"DROP TABLE {rejects}")
    print(f"{rejects} 드롭 완료")
else:
    print(f"{rejects} 원래 없음")

print("\n✅ 정리 완료 — 이제 load_aers_master.py를 처음부터 다시 돌리면 됩니다.")
