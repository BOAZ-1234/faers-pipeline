import os
from dotenv import load_dotenv
from pyspark.sql import SparkSession

# 1. AWS 및 Spark 세션 세팅 (기존과 완벽히 동일)
load_dotenv()
aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"

print("⏳ Spark 세션을 시작합니다... (데이터 검증 모드)")
spark = SparkSession.builder \
    .appName("Verify_Loaded_Data") \
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

# 2. 데이터 확인용 헬퍼 함수
def check_table(table_name, description):
    print(f"\n==================================================")
    print(f" 📊 {description} 확인 중... \n 📍 경로: {table_name}")
    print(f"==================================================")
    
    try:
        # Iceberg 테이블 읽기
        df = spark.table(table_name)
        
        # 데이터 총 건수 카운트
        total_count = df.count()
        print(f"✅ 총 데이터 건수: {total_count:,} 건")
        
        # 상위 5개 행(Row) 출력
        print("\n👀 상위 5개 데이터 미리보기:")
        df.show(5)
        
    except Exception as e:
        print(f"❌ 테이블을 읽을 수 없습니다: {e}")

# 3. 테이블 검증 실행
# (1) [A세부3 - 1단계] 고유 약물명 사전 (Silver)
check_table("my_catalog.stage_b_silver.dict_unique_drugs", "[1단계] FAERS 고유 약물명 추출 데이터")

# (2) [A세부3 - 2단계] OpenFDA Label (Bronze)
check_table("my_catalog.stage_a_raw.openfda_label", "[2단계] OpenFDA Label 데이터 (14개 병합본)")

# (3) [A세부3 - 2단계] OpenFDA Drugs@FDA (Bronze)
check_table("my_catalog.stage_a_raw.openfda_drugsfda", "[2단계] OpenFDA Drugs@FDA 데이터")

print("\n🎉 모든 데이터 검증이 완료되었습니다!")