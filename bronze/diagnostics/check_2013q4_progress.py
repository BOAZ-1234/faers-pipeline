# 2013Q4 파일이 faers_drug/faers_reac에 정확히 얼마나 들어갔는지 확인합니다 (삭제는 안 함, 조회만).
# 로컬 zip에서 2013Q4의 정확한 primaryid 목록을 뽑아 그 ID들로만 좁혀서 COUNT하기 때문에
# 전체 테이블 스캔이 아니라 2013Q4에 해당하는 데이터 파일만 건드립니다.

import sys
import os
import zipfile
from dotenv import load_dotenv
from pyspark.sql import SparkSession

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 1. 로컬 zip에서 2013Q4의 정확한 primaryid 목록 추출 (S3 비용 없음)
with zipfile.ZipFile("data/faers_not_load/faers_ascii_2013q4.zip") as z:
    demo_file = [f for f in z.namelist() if "DEMO" in f.upper() and f.upper().endswith(".TXT")][0]
    with z.open(demo_file) as f:
        header = f.readline().decode("utf-8").strip().split("$")
        pid_idx = header.index("primaryid")
        ids = []
        for line in f:
            data = line.decode("utf-8", errors="replace").strip().split("$")
            if len(data) == len(header):
                ids.append(data[pid_idx])

print(f"📦 2013Q4 파일의 정확한 primaryid 개수: {len(ids)}개")

load_dotenv()
aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"

print("⏳ Spark 세션을 시작합니다...")
spark = SparkSession.builder \
    .appName("Check_2013Q4_Progress") \
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

ids_df = spark.createDataFrame([(i,) for i in ids], ["primaryid"])
ids_df.createOrReplaceTempView("ids_2013q4")

for table in ["faers_demo", "faers_drug", "faers_reac"]:
    full_name = f"my_catalog.stage_a_raw.{table}"
    count = spark.sql(f"""
        SELECT COUNT(*) AS c FROM {full_name}
        WHERE primaryid IN (SELECT primaryid FROM ids_2013q4)
    """).collect()[0]["c"]
    print(f"   {table}: 2013Q4 관련 행 {count}개 이미 적재됨")

print("\n✅ 확인 완료 (아무것도 삭제하지 않았습니다).")
