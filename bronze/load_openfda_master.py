import os
import glob
from dotenv import load_dotenv
from pyspark.sql import SparkSession
from pyspark.sql.functions import explode, lit

# 1. AWS 및 Spark 세션 세팅
load_dotenv()
aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"

print("⏳ Spark 세션을 시작합니다...")
spark = SparkSession.builder \
    .appName("Load_OpenFDA_Master") \
    .config("spark.driver.memory", "6g") \
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

# 브론즈 네임스페이스 확인
spark.sql("CREATE NAMESPACE IF NOT EXISTS my_catalog.stage_a_raw")

# 2. [Label 데이터] 파싱 및 적재 (양방향 완벽 대조 동기화 모드)
print("\n🔍 OpenFDA Drug Label 데이터 파싱 중 (양방향 완벽 동기화 모드)...")

label_files = sorted(glob.glob("data/label/*.json"))
print(f"총 {len(label_files)}개의 파일을 발견했습니다. 순차 적재를 시작합니다!")

for i, file_path in enumerate(label_files):
    print(f"[{i+1}/{len(label_files)}] {file_path} 처리 및 S3 적재 중...")
    
    df = spark.read.option("multiline", "true").json(file_path)
    exploded_df = df.select(explode("results").alias("res")).select("res.*")
    
    if i == 0:
        write_mode = "overwrite"
    else:
        write_mode = "append"
        
        # 🛡️ [양방향 스키마 대조 및 싱크 맞추기]
        target_table_df = spark.table("my_catalog.stage_a_raw.openfda_label")
        
        # 1단계: S3 테이블에는 있는데 현재 파일에 없으면 -> 현재 파일에 Null 채우기
        for target_col in target_table_df.columns:
            if target_col not in exploded_df.columns:
                target_type = target_table_df.schema[target_col].dataType
                exploded_df = exploded_df.withColumn(target_col, lit(None).cast(target_type))
                
        # 2단계: 현재 파일에는 있는데 S3 테이블(기존 데이터)에 없으면 -> 과거 테이블 호환을 위해 
        # mergeSchema 옵션이 새로운 기둥을 유연하게 확장해 줍니다.
    
    exploded_df.write \
        .format("iceberg") \
        .option("mergeSchema", "true") \
        .mode(write_mode) \
        .saveAsTable("my_catalog.stage_a_raw.openfda_label")

print("✅ Label 데이터 14개 분할 적재 완료! [my_catalog.stage_a_raw.openfda_label]")


# 3. [Drugs@FDA 데이터] 파싱 및 적재
print("\n🔍 OpenFDA Drugs@FDA 데이터 파싱 중...")
drugsfda_raw_df = spark.read.option("multiline", "true").json("data/drugsfda/*.json")
drugsfda_df = drugsfda_raw_df.select(explode("results").alias("res")).select("res.*")

print("💾 Drugs@FDA 데이터를 S3에 적재합니다...")
drugsfda_df.write \
    .format("iceberg") \
    .mode("overwrite") \
    .saveAsTable("my_catalog.stage_a_raw.openfda_drugsfda")
print("✅ Drugs@FDA 데이터 적재 완료! [my_catalog.stage_a_raw.openfda_drugsfda]")

print("\n🎉 모든 마스터 데이터가 성공적으로 S3 레이크에 둥지를 틀었습니다!")