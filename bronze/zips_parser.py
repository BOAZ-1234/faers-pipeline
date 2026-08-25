import os
import glob
import zipfile
from dotenv import load_dotenv
from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType

# 1. AWS 열쇠 장착
load_dotenv()
aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"

# 2. Spark 세션 시작
print("⏳ Spark 세션을 시작합니다...")
spark = SparkSession.builder \
    .appName("FAERS_StageA_Raw_Pipeline") \
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

# 🌟 3. 우리 팀 전용 브론즈 네임스페이스 생성
NAMESPACE = "stage_a_raw"
spark.sql(f"CREATE NAMESPACE IF NOT EXISTS my_catalog.{NAMESPACE}")

# 처리할 타겟 파일과 분리된 테이블 이름 딕셔너리 
TARGET_MAPPING = {
    "DEMO": f"my_catalog.{NAMESPACE}.faers_demo",
    "DRUG": f"my_catalog.{NAMESPACE}.faers_drug",
    "REAC": f"my_catalog.{NAMESPACE}.faers_reac"
}
CHUNK_SIZE = 50000

# 4. Iceberg 적재 함수 (다형성 지원)
def write_to_iceberg(chunk_data, header, table_name, file_type):
    if not chunk_data:
        return
        
    schema = StructType([StructField(col, StringType(), True) for col in header])
    df = spark.createDataFrame(chunk_data, schema)
    df.createOrReplaceTempView("temp_chunk")
    
    if not spark.catalog.tableExists(table_name):
        print(f"      -> 🏗️ [{table_name}] 최초 생성 및 적재 완료!")
        df.write.format("iceberg").saveAsTable(table_name)
    else:
        # 1:1 관계인 환자 정보는 멱등성을 위해 덮어쓰기(Upsert)
        if file_type == "DEMO":
            spark.sql(f"""
            MERGE INTO {table_name} t
            USING temp_chunk s
            ON t.primaryid = s.primaryid AND t.caseversion = s.caseversion
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
            """)
        # 1:N 관계인 약물, 부작용 정보는 Raw 단계의 원칙에 따라 무조건 밀어넣기(Append)
        else:
            df.write.format("iceberg").mode("append").saveAsTable(table_name)

# 5. 모든 ZIP 파일 연속 파싱 릴레이 작전!
zip_files = sorted(glob.glob("data/*.zip"))
print(f"\n📦 총 {len(zip_files)}개의 ZIP 파일을 발견했습니다. 파이프라인 가동을 시작합니다!\n" + "="*60)

for zip_path in zip_files:
    file_name = os.path.basename(zip_path)
    print(f"\n🔄 [{file_name}] 압축 해제 없이 다이렉트 스트리밍 시작...")
    
    with zipfile.ZipFile(zip_path, 'r') as z:
        file_list = z.namelist()
        
        # DEMO -> DRUG -> REAC 순차적으로 찾아서 각각의 테이블에 꽂아넣기
        for file_type, table_name in TARGET_MAPPING.items():
            target_file = [f for f in file_list if file_type in f.upper() and f.endswith('.txt')]
            
            if target_file:
                target = target_file[0]
                print(f"  🚀 [{target}] -> [{table_name}] 적재 진행 중...")
                
                with z.open(target) as f:
                    # 첫 줄 헤더 파싱
                    header = f.readline().decode('utf-8').strip().split('$')
                    chunk = []
                    total_count = 0
                    
                    for line in f:
                        data = line.decode('utf-8').strip().split('$')
                        # 컬럼 개수가 정상인 데이터만 캡처
                        if len(data) == len(header):
                            chunk.append(data)
                        
                        # 청크 사이즈 도달 시 발사
                        if len(chunk) >= CHUNK_SIZE:
                            total_count += len(chunk)
                            write_to_iceberg(chunk, header, table_name, file_type)
                            chunk = []
                    
                    # 마지막 남은 자투리 발사
                    if chunk:
                        total_count += len(chunk)
                        write_to_iceberg(chunk, header, table_name, file_type)
                        
                print(f"  ✅ [{file_type}] 총 {total_count}건 처리 완료!")
            else:
                print(f"  ⚠️ [{file_name}] 안에 {file_type} 파일이 없어 건너뜁니다.")

print("\n🎉 모든 ZIP 파일의 [DEMO, DRUG, REAC] 분리 적재 파이프라인이 완벽하게 종료되었습니다!")