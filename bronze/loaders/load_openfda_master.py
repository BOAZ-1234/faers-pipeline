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

# 2. [Label 데이터] 파싱 및 적재 (수동 스키마 완벽 제어 모드)
print("\n🔍 OpenFDA Drug Label 데이터 파싱 중 (수동 스키마 제어 모드)...")

label_files = sorted(glob.glob("data/label/*.json"))
print(f"총 {len(label_files)}개의 파일을 발견했습니다. 순차 적재를 시작합니다!")

for i, file_path in enumerate(label_files):
    print(f"[{i+1}/{len(label_files)}] {file_path} 처리 및 S3 적재 중...")
    
    df = spark.read.option("multiline", "true").json(file_path)
    exploded_df = df.select(explode("results").alias("res")).select("res.*")
    
    table_name = "my_catalog.stage_a_raw.openfda_label"
    
    if i == 0:
        # 첫 번째 파일로 기준 테이블을 생성합니다.
        exploded_df.writeTo(table_name).createOrReplace()
    else:
        target_table_df = spark.table(table_name)
        
        # 🛡️ [수동 방어 1단계: 새로운 컬럼 추가] 
        # 파일에는 있는데 S3 테이블에 없는 '새로운 기둥'을 찾아 직접 ALTER TABLE을 날립니다.
        new_cols = []
        for col_name in exploded_df.columns:
            if col_name not in target_table_df.columns:
                col_type = exploded_df.schema[col_name].dataType.simpleString()
                # 백틱(`)을 사용해 띄어쓰기 등 특수문자 에러 방지
                new_cols.append(f"`{col_name}` {col_type}")
        
        if new_cols:
            print(f"    🌟 [스키마 진화] {len(new_cols)}개의 새로운 컬럼 감지! 테이블을 확장합니다.")
            alter_query = f"ALTER TABLE {table_name} ADD COLUMNS ({', '.join(new_cols)})"
            spark.sql(alter_query)
            
        # 🛡️ [수동 방어 2단계: 최신 스키마 갱신]
        # 방금 ALTER TABLE로 기둥을 추가했으니, 테이블 설계도를 다시 읽어옵니다.
        updated_table_df = spark.table(table_name)
        
        # 🛡️ [수동 방어 3단계: 누락된 컬럼 처리] 
        # 테이블에는 있는데 파일에 없는 기둥들을 찾아 강제로 Null을 채워 넣습니다.
        for target_col in updated_table_df.columns:
            if target_col not in exploded_df.columns:
                target_type = updated_table_df.schema[target_col].dataType
                exploded_df = exploded_df.withColumn(target_col, lit(None).cast(target_type))
                
        # 🛡️ [수동 방어 4단계: 순서 맞추기] (🔥 핵심)
        # 컬럼 개수가 같아도 순서가 다르면 뻗기 때문에, 테이블과 똑같은 순서로 재배열합니다.
        exploded_df = exploded_df.select(*updated_table_df.columns)
        
        # 🚀 [최종 적재] 두 데이터의 구조가 100% 동일해졌으므로 무조건 성공합니다!
        exploded_df.writeTo(table_name).append()

print("✅ Label 데이터 14개 분할 적재 완료! [my_catalog.stage_a_raw.openfda_label]")


# 3. [Drugs@FDA 데이터] 파싱 및 적재
print("\n🔍 OpenFDA Drugs@FDA 데이터 파싱 중...")
drugsfda_raw_df = spark.read.option("multiline", "true").json("data/drugsfda/*.json")
drugsfda_df = drugsfda_raw_df.select(explode("results").alias("res")).select("res.*")

print("💾 Drugs@FDA 데이터를 S3에 적재합니다...")
drugsfda_df.writeTo("my_catalog.stage_a_raw.openfda_drugsfda").createOrReplace()
print("✅ Drugs@FDA 데이터 적재 완료! [my_catalog.stage_a_raw.openfda_drugsfda]")

print("\n🎉 모든 마스터 데이터가 성공적으로 S3 레이크에 둥지를 틀었습니다!")