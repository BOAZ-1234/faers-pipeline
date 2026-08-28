import os
from dotenv import load_dotenv
from pyspark.sql import SparkSession

# 1. AWS 열쇠 장착
load_dotenv()
aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"

# 2. Spark 세션 시작 (메모리 4GB 강제 할당 추가!)
print("⏳ Spark 세션을 시작하여 S3/Iceberg에 연결합니다...")
spark = SparkSession.builder \
    .appName("FAERS_Data_Checker") \
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

# 3. 데이터 적재 상태 확인 쿼리 (무거운 카운트 제외, 최근 날짜만 빠르게 확인)
print("\n📅 [가장 최근 분기 파악] 최근 적재된 데이터의 보고일자(fda_dt) 샘플 TOP 10")
spark.sql("""
    SELECT primaryid, event_dt, fda_dt 
    FROM my_catalog.stage_a_raw.faers_demo 
    ORDER BY fda_dt DESC NULLS LAST 
    LIMIT 10
""").show()

# 4. 데이터 적재 상태 확인 쿼리 (가장 과거 데이터 확인)
print("\n📅 [과거 데이터 파악] 가장 오래된 데이터의 보고일자(fda_dt) 샘플 TOP 10")
spark.sql("""
    SELECT primaryid, event_dt, fda_dt 
    FROM my_catalog.stage_a_raw.faers_demo 
    WHERE fda_dt IS NOT NULL
    ORDER BY fda_dt ASC 
    LIMIT 10
""").show()

print("\n✅ 데이터 확인이 완료되었습니다! (메모리 부족 방지를 위해 전체 건수 카운트는 생략했습니다.)")