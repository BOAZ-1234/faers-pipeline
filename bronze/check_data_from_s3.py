import os
from dotenv import load_dotenv
from pyspark.sql import SparkSession

# 1. AWS 열쇠 장착
load_dotenv()
aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"

print("⏳ Spark 세션을 켜고 S3 데이터 레이크에 접속합니다...")
spark = SparkSession.builder \
    .appName("FAERS_Data_Verification") \
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

# 2. 방금 적재한 테이블 불러오기 (이미지 경로 기준)
# 만약 아까 최종 코드로 stage_a_raw에 넣으셨다면 "my_catalog.stage_a_raw.faers_demo" 로 변경해주세요!
TABLE_NAME = "my_catalog.default.bronze_demo"

print(f"\n🚀 S3에서 [{TABLE_NAME}] 테이블을 조회합니다...")
df = spark.table(TABLE_NAME)

# 3. 데이터 총 건수 확인
total_records = df.count()
print(f"📊 총 적재된 데이터 건수: {total_records:,}건")

# 4. 데이터 5줄 미리보기
print("\n📝 데이터 샘플 5줄:")
df.show(5)