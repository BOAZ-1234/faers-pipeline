import os
import zipfile
from dotenv import load_dotenv
from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType

# 1. AWS 열쇠 장착
load_dotenv()
aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"

# 2. Spark 세션 켜기 (S3 + Iceberg 세팅 완료)
print("⏳ Spark 세션을 시작합니다...")
spark = SparkSession.builder \
    .appName("FAERS_DEMO_Ingestion") \
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

# 3. Iceberg에 MERGE (Upsert) 하는 함수
def write_to_iceberg(chunk_data, header, table_name="my_catalog.default.bronze_demo"):
    if not chunk_data:
        return
        
    # 모든 컬럼을 일단 문자열(String)로 받습니다 (Bronze 단계의 원칙)
    schema = StructType([StructField(col, StringType(), True) for col in header])
    df = spark.createDataFrame(chunk_data, schema)
    
    # 임시 뷰 생성
    df.createOrReplaceTempView("temp_chunk")
    
    # 테이블이 존재하는지 확인
    table_exists = spark.catalog.tableExists(table_name)
    
    if not table_exists:
        print(f"  -> 🏗️ [{table_name}] 테이블이 없어 새로 생성하고 최초 적재합니다.")
        df.write.format("iceberg").saveAsTable(table_name)
    else:
        print(f"  -> 🔄 기존 테이블에 MERGE (Upsert) 쿼리를 실행합니다.")
        merge_query = f"""
        MERGE INTO {table_name} t
        USING temp_chunk s
        ON t.primaryid = s.primaryid AND t.caseversion = s.caseversion
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
        """
        spark.sql(merge_query)

# 4. ZIP 파일 스트리밍 파싱 및 청크(Chunk) 처리
zip_path = "data/faers_ascii_2026q2.zip"  # 회원님의 실제 압축파일 이름에 맞게 수정해주세요!
CHUNK_SIZE = 50000  # 한 번에 5만 건씩 메모리에 올려서 전송

with zipfile.ZipFile(zip_path, 'r') as z:
    demo_file = [f for f in z.namelist() if 'DEMO' in f.upper() and f.endswith('.txt')]
    
    if demo_file:
        target = demo_file[0]
        print(f"\n🚀 [{target}] 파싱 및 S3 적재를 시작합니다!")
        
        with z.open(target) as f:
            header = f.readline().decode('utf-8').strip().split('$')
            
            chunk = []
            total_count = 0
            
            for line in f:
                data = line.decode('utf-8').strip().split('$')
                
                # 에러 방지: 헤더와 데이터 개수가 일치하는 정상 데이터만 적재
                if len(data) == len(header):
                    chunk.append(data)
                else:
                    # TODO: 나중에 Dead Letter Queue 로직 추가할 곳
                    pass 
                
                # 5만 건이 모이면 S3로 발사!
                if len(chunk) >= CHUNK_SIZE:
                    total_count += len(chunk)
                    print(f"📦 {total_count}건 도달! S3로 전송 중...")
                    write_to_iceberg(chunk, header)
                    chunk = [] # 메모리 비우기
            
            # 마지막 남은 자투리 데이터 발사
            if chunk:
                total_count += len(chunk)
                print(f"📦 마지막 자투리 포함 총 {total_count}건 전송 중...")
                write_to_iceberg(chunk, header)
                
        print("\n🎉 모든 데이터가 AWS S3 Data Lake에 성공적으로 적재되었습니다!")
    else:
        print("DEMO 파일을 찾을 수 없습니다.")