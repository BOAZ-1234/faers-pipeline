# AERS 원본 데이터(2004Q1 ~ 2012Q3)를 브론즈 계층의 전용 테이블에 쌓는 적재 스크립트입니다.
# data/aers_not_load/ 폴더 안의 모든 aers_*.zip 파일을 다이렉트 스트리밍합니다.

import sys
import os
import glob
import zipfile
from dotenv import load_dotenv

# Windows 콘솔 기본 코드페이지(cp949 등)에서도 이모지 출력이 깨지지 않도록 강제 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType, IntegerType
from pyspark.sql.functions import col, lit
from pipeline_common import (
    manifest_status, upsert_manifest, ensure_partitioned_by_source_zip, delete_partial_rows,
)

BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"

# 🌟 우리 팀 전용 브론즈 네임스페이스
NAMESPACE = "stage_a_raw"

# AERS 전용 타겟 매핑 (faers_ -> aers_ 로 테이블명 완전 분리!)
TARGET_MAPPING = {
    "DEMO": f"my_catalog.{NAMESPACE}.aers_demo",
    "DRUG": f"my_catalog.{NAMESPACE}.aers_drug",
    "REAC": f"my_catalog.{NAMESPACE}.aers_reac"
}

# AERS 전용 격리 테이블
REJECTS_TABLE = f"my_catalog.{NAMESPACE}.aers_load_rejects"
REJECTS_SCHEMA = StructType([
    StructField("source_zip", StringType(), True),
    StructField("source_txt", StringType(), True),
    StructField("file_type", StringType(), True),
    StructField("line_no", IntegerType(), True),
    StructField("expected_cols", IntegerType(), True),
    StructField("actual_cols", IntegerType(), True),
    StructField("raw_line", StringType(), True),
])
CHUNK_SIZE = 30000

spark = None


# 💡 [NEW] AERS 전용 스키마 매핑 및 클리닝 함수
def harmonize_aers_schema(df, table_type):
    # 1. 컬럼명 일괄 소문자화 및 공백/BOM 정제
    for c in df.columns:
        clean_col = c.replace('\ufeff', '').strip().lower()
        if c != clean_col:
            df = df.withColumnRenamed(c, clean_col)

    # 2. AERS 최종 폼(2012년 기준 최고 스펙) 정의 (source_zip: 재시작 안전장치용)
    target_schemas = {
        "DEMO": ['isr', 'case', 'i_f_cod', 'foll_seq', 'image', 'event_dt', 'mfr_dt', 'fda_dt', 'rept_cod', 'mfr_num', 'mfr_sndr', 'age', 'age_cod', 'gndr_cod', 'e_sub', 'wt', 'wt_cod', 'rept_dt', 'occp_cod', 'death_dt', 'to_mfr', 'confid', 'reporter_country', 'source_zip'],
        "DRUG": ['isr', 'drug_seq', 'role_cod', 'drugname', 'val_vbm', 'route', 'dose_vbm', 'dechal', 'rechal', 'lot_num', 'exp_dt', 'nda_num', 'source_zip'],
        "REAC": ['isr', 'pt', 'source_zip']
    }
    
    target_cols = target_schemas.get(table_type)

    # 3. 2004~2005년 파일처럼 'reporter_country'가 없는 경우 Null로 빈 기둥 세우기
    for tc in target_cols:
        if tc not in df.columns:
            df = df.withColumn(tc, lit(None).cast("string"))

    # 4. 순서 재정렬 후 반환
    return df.select(*target_cols)


def classify_line(raw_line, header, file_name, target, file_type, line_no):
    decoded_line = raw_line.decode('utf-8', errors='replace')
    stripped = decoded_line.strip()

    # AERS 원본 행은 마지막 필드 뒤에 필드가 아니라 종결용 '$'가 하나 더 붙어있다
    # (헤더 줄에는 이게 없다). 그것만 떼고 나머지는 그대로 쪼갠다.
    line_for_split = stripped[:-1] if stripped.endswith('$') else stripped
    data = line_for_split.split('$')

    if len(data) == len(header):
        return data, None

    reject = [
        file_name, target, file_type, line_no,
        len(header), len(data), decoded_line.strip(),
    ]
    return None, reject


def write_to_iceberg(chunk_data, header, table_name, file_type, source_zip):
    if not chunk_data:
        return

    schema = StructType([StructField(col, StringType(), True) for col in header])
    df = spark.createDataFrame(chunk_data, schema)

    # 어느 zip에서 왔는지 남겨서, 재시작 시 이 zip 분량만 정확히 지우고 다시 채울 수 있게 함
    df = df.withColumn("source_zip", lit(source_zip))

    # AERS 마법의 클리닝 함수 적용
    df = harmonize_aers_schema(df, file_type)

    df.createOrReplaceTempView("temp_chunk")

    ensure_partitioned_by_source_zip(spark, table_name)

    if not spark.catalog.tableExists(table_name):
        print(f"      -> 🏗️ [{table_name}] 최초 생성 및 적재 완료!")
        df.write.format("iceberg").saveAsTable(table_name)
        ensure_partitioned_by_source_zip(spark, table_name)
    else:
        if file_type == "DEMO":
            # 💡 [CRITICAL] AERS는 primaryid 대신 고유식별자 ISR을 기준으로 Merge!
            spark.sql(f"""
            MERGE INTO {table_name} t
            USING temp_chunk s
            ON t.isr = s.isr
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
            """)
        else:
            df.write.format("iceberg").mode("append").saveAsTable(table_name)


def write_rejects_to_iceberg(reject_chunk):
    if not reject_chunk:
        return

    df = spark.createDataFrame(reject_chunk, REJECTS_SCHEMA)
    if not spark.catalog.tableExists(REJECTS_TABLE):
        df.write.format("iceberg").saveAsTable(REJECTS_TABLE)
    else:
        df.write.format("iceberg").mode("append").saveAsTable(REJECTS_TABLE)


def run():
    global spark

    load_dotenv()
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

    print("⏳ Spark 세션을 시작합니다...")
    spark = SparkSession.builder \
        .appName("AERS_Legacy_Raw_Pipeline") \
        .master("local[4]") \
        .config("spark.driver.memory", "6g") \
        .config("spark.sql.shuffle.partitions", "8") \
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

    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS my_catalog.{NAMESPACE}")

    # 💡 [CRITICAL] aers_not_load 폴더의 aers_ 로 시작하는 파일만 타겟팅!
    zip_files = sorted(glob.glob("data/aers_not_load/aers_*.zip"), reverse=True)
    print(f"\n📦 총 {len(zip_files)}개의 AERS ZIP 파일을 발견했습니다. 파이프라인 가동을 시작합니다!\n" + "=" * 60)

    for zip_path in zip_files:
        file_name = os.path.basename(zip_path)
        print(f"\n🔄 [{file_name}] 압축 해제 없이 다이렉트 스트리밍 시작...")

        with zipfile.ZipFile(zip_path, 'r') as z:
            file_list = z.namelist()

            for file_type, table_name in TARGET_MAPPING.items():
                target_file = [f for f in file_list if file_type in f.upper() and f.upper().endswith('.TXT')]

                if not target_file:
                    print(f"  ⚠️ [{file_name}] 안에 {file_type} 파일이 없어 건너뜁니다.")
                    continue

                status = manifest_status(spark, NAMESPACE, "aers", file_name, file_type)
                if status == "complete":
                    print(f"  ⏭️ [{file_name}] {file_type} 이미 완료됨(load_manifest) — 건너뜁니다.")
                    continue
                if status == "in_progress":
                    print(f"  🔧 [{file_name}] {file_type} 중간에 멈춘 기록 발견 — 부분 적재분 정리 후 재처리합니다.")
                    delete_partial_rows(spark, table_name, file_name)
                    delete_partial_rows(spark, REJECTS_TABLE, file_name)

                upsert_manifest(spark, NAMESPACE, "aers", file_name, file_type, "in_progress")

                target = target_file[0]
                print(f"  🚀 [{target}] -> [{table_name}] 적재 진행 중...")

                with z.open(target) as f:
                    header = f.readline().decode('utf-8').strip().split('$')
                    chunk = []
                    reject_chunk = []
                    total_count = 0
                    total_rejected = 0
                    line_no = 1

                    for raw_line in f:
                        line_no += 1
                        row, reject = classify_line(raw_line, header, file_name, target, file_type, line_no)
                        if row is not None:
                            chunk.append(row)
                        else:
                            reject_chunk.append(reject)

                        if len(chunk) >= CHUNK_SIZE:
                            total_count += len(chunk)
                            write_to_iceberg(chunk, header, table_name, file_type, file_name)
                            chunk = []
                        if len(reject_chunk) >= CHUNK_SIZE:
                            total_rejected += len(reject_chunk)
                            write_rejects_to_iceberg(reject_chunk)
                            reject_chunk = []

                    if chunk:
                        total_count += len(chunk)
                        write_to_iceberg(chunk, header, table_name, file_type, file_name)
                    if reject_chunk:
                        total_rejected += len(reject_chunk)
                        write_rejects_to_iceberg(reject_chunk)

                upsert_manifest(spark, NAMESPACE, "aers", file_name, file_type, "complete",
                                 row_count=total_count, rejected_count=total_rejected)
                print(f"  ✅ [{file_type}] 총 {total_count}건 처리 완료! (격리 레코드 {total_rejected}건)")

    print("\n🎉 고대 유물 [AERS] 전용 분리 적재 파이프라인이 완벽하게 종료되었습니다!")


if __name__ == "__main__":
    run()