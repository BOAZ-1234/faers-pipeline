# faers_demo/faers_drug/faers_reac에 source_zip 컬럼을 정식으로 추가한다.
#
# 왜 필요한가: load_faers_master.py는 원래 source_zip을 써서 저장하도록 짜여 있는데,
# 지금 라이브 테이블엔 그 컬럼이 없다. 그래서 다음 분기(예: 2026Q3)가 나와서 이 스크립트를
# 그대로 재실행하면 스키마 불일치로 에러가 난다. 지금 스키마 진화(ALTER TABLE ADD COLUMN)로
# 컬럼만 추가해두면 — 이건 메타데이터만 바뀌는 작업이라 8,400만 행을 다시 쓰지 않는다.
#
# 결과: 기존 행(2013Q3/2013Q4 백필분 포함, 그 이전 전량)은 source_zip이 NULL로 남는다
# (원래부터 어느 zip인지 기록이 없었으니 소급 채우기는 이번 범위 밖 — 지난번 anti-join 스크립트로
# 이미 어느 zip 소속인지는 알아냈지만, 그걸 실제로 채워넣는 건 8,400만 행을 다시 쓰는 무거운
# 작업이라 별개로 판단할 사안). 앞으로 새로 적재되는 분기부터는 자동으로 채워진다.

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
NAMESPACE = "stage_a_raw"

spark = SparkSession.builder \
    .appName("Add_Source_Zip_Column") \
    .config("spark.driver.memory", "4g") \
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

TABLES = ["faers_demo", "faers_drug", "faers_reac"]

for t in TABLES:
    full_name = f"my_catalog.{NAMESPACE}.{t}"
    cols = [c.lower() for c in spark.table(full_name).columns]
    if "source_zip" in cols:
        print(f"⏭️ [{t}] 이미 source_zip 컬럼 있음 — 스킵")
        continue

    print(f"🔧 [{t}] source_zip 컬럼 추가 중...")
    spark.sql(f"ALTER TABLE {full_name} ADD COLUMN source_zip STRING")

    # 앞으로 새 분기 적재부터 파티션 진화도 걸어둔다 (pipeline_common.ensure_partitioned_by_source_zip과 동일 동작)
    try:
        spark.sql(f"ALTER TABLE {full_name} ADD PARTITION FIELD source_zip")
        print(f"   파티션 필드로도 등록함")
    except Exception as e:
        print(f"   ⚠️ 파티션 등록 스킵: {e}")

    new_cols = spark.table(full_name).columns
    print(f"✅ [{t}] 완료. 컬럼: {new_cols}")

print("\n🎉 전부 완료. 기존 행은 source_zip=NULL, 다음 분기 적재부터 자동으로 채워짐.")
spark.stop()
