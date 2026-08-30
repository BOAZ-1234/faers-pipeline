# 브론즈 계층(stage_a_raw.faers_drug)의 원본 약물 데이터에서 중복을 제거한 고유 약물명(Unique Drugs)을 추출하고,
# 이를 실버 계층(stage_b_silver.dict_unique_drugs) 사전에 Iceberg 포맷으로 영구 적재하는 핵심 파이프라인 스크립트입니다.
import os
from dotenv import load_dotenv
from pyspark.sql import SparkSession

# 1. AWS 및 Spark 세션 세팅
load_dotenv()
aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"

print("⏳ Spark 세션을 시작합니다...")
spark = SparkSession.builder \
    .appName("Extract_Unique_Drugs") \
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
    .config("spark.hadoop.fs.s3a.path.style.access", "true") \
    .getOrCreate()

# 2. 고유 약물명 추출 쿼리 (가상 메모리에만 올려둠)
print("\n🔍 4,000만 건 데이터 중복 제거 및 추출 중...")
unique_drugs_df = spark.sql("""
    SELECT DISTINCT 
        UPPER(TRIM(drugname)) AS clean_drugname
    FROM my_catalog.stage_a_raw.faers_drug
    WHERE drugname IS NOT NULL AND TRIM(drugname) != ''
""")

# 3. 실버 계층 네임스페이스 생성 및 S3에 '먼저' 저장
print("\n💾 추출된 고유 약물 사전을 S3에 바로 굽습니다! (이 구간에서 가장 오랜 시간이 걸립니다...)")
spark.sql("CREATE NAMESPACE IF NOT EXISTS my_catalog.stage_b_silver")

unique_drugs_df.write \
    .format("iceberg") \
    .mode("overwrite") \
    .saveAsTable("my_catalog.stage_b_silver.dict_unique_drugs")
print("✅ S3 저장 완료! [my_catalog.stage_b_silver.dict_unique_drugs]")

# 4. 저장된 테이블에서 결과 및 건수 확인 (Iceberg 메타데이터를 읽으므로 아주 빠릅니다!)
print("\n📊 저장된 데이터 샘플 및 총 건수 확인")
saved_df = spark.table("my_catalog.stage_b_silver.dict_unique_drugs")
saved_df.show(20, truncate=False)

total_unique_count = saved_df.count()
print(f"\n✅ 최종 고유 약물명 총 개수: {total_unique_count}건")