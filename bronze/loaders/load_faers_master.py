# FAERS 원본 데이터를 완벽하게 브론즈 계층에 쌓는 메인 마스터 적재 스크립트입니다.
# data/faers_not_load/ 폴더 안의 모든 FAERS ZIP 파일을 찾아 연속으로 압축 해제 없이 다이렉트 스트리밍합니다.
# 2012Q4 ~ 2014Q2 구간의 더러운 컬럼 구조(유령 문자, 공백, 누락 컬럼)를 최신 규격으로 자동 조화(Harmonize)하여 적재합니다.

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
from pyspark.sql.functions import col, lit  # 💡 추가된 라이브러리
from pipeline_common import (
    manifest_status, upsert_manifest, ensure_partitioned_by_source_zip, delete_partial_rows,
)

BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"

# 🌟 우리 팀 전용 브론즈 네임스페이스
NAMESPACE = "stage_a_raw"

# 처리할 타겟 파일과 분리된 테이블 이름 딕셔너리
TARGET_MAPPING = {
    "DEMO": f"my_catalog.{NAMESPACE}.faers_demo",
    "DRUG": f"my_catalog.{NAMESPACE}.faers_drug",
    "REAC": f"my_catalog.{NAMESPACE}.faers_reac"
}

# 설계 문서 4-1: 컬럼 개수가 안 맞는 실패 레코드는 버리지 않고 이 테이블에 격리한다.
REJECTS_TABLE = f"my_catalog.{NAMESPACE}.faers_load_rejects"
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


# 💡 [NEW] 2012~2014년 과거 FAERS 데이터를 최신 스키마 규격으로 완벽 매핑 및 클리닝하는 함수
def harmonize_legacy_schema(df, table_type):
    # 1. 더러운 컬럼명 일괄 정제 (BOM 제거, 양옆 공백 제거, 소문자화)
    for c in df.columns:
        clean_col = c.replace('\ufeff', '').strip().lower()
        if c != clean_col:
            df = df.withColumnRenamed(c, clean_col)

    # 2. 이름이 아예 바뀐 컬럼들 수동 매핑 
    if table_type == "DEMO" and "gndr_cod" in df.columns:
        df = df.withColumnRenamed("gndr_cod", "sex")
    
    elif table_type == "DRUG" and "lot_nbr" in df.columns:
        df = df.withColumnRenamed("lot_nbr", "lot_num")

    # 3. 최신 타겟 스키마(정답지) 정의 (source_zip: 재시작 안전장치용, 어느 zip에서 왔는지 추적)
    target_schemas = {
        "DEMO": ['primaryid', 'caseid', 'caseversion', 'i_f_code', 'event_dt', 'mfr_dt', 'init_fda_dt', 'fda_dt', 'rept_cod', 'auth_num', 'mfr_num', 'mfr_sndr', 'lit_ref', 'age', 'age_cod', 'age_grp', 'sex', 'e_sub', 'wt', 'wt_cod', 'rept_dt', 'to_mfr', 'occp_cod', 'reporter_country', 'occr_country', 'source_zip'],
        "DRUG": ['primaryid', 'caseid', 'drug_seq', 'role_cod', 'drugname', 'prod_ai', 'val_vbm', 'route', 'dose_vbm', 'cum_dose_chr', 'cum_dose_unit', 'dechal', 'rechal', 'lot_num', 'exp_dt', 'nda_num', 'dose_amt', 'dose_unit', 'dose_form', 'dose_freq', 'source_zip'],
        "REAC": ['primaryid', 'caseid', 'pt', 'drug_rec_act', 'source_zip']
    }
    
    target_cols = target_schemas.get(table_type)

    # 4. 과거에 없었던 신규 컬럼들을 Null 빈칸으로 채워서 뼈대 세우기
    for tc in target_cols:
        if tc not in df.columns:
            df = df.withColumn(tc, lit(None).cast("string"))

    # 5. 최신 스키마와 완벽하게 똑같은 순서로 컬럼을 재정렬하여 반환
    return df.select(*target_cols)


def classify_line(raw_line, header, file_name, target, file_type, line_no):
    """
    원본 라인 1개를 파싱해서 (정상 row, None) 또는 (None, 격리 레코드)를 반환한다.
    """
    decoded_line = raw_line.decode('utf-8', errors='replace')
    data = decoded_line.strip().split('$')

    if len(data) == len(header):
        return data, None

    reject = [
        file_name, target, file_type, line_no,
        len(header), len(data), decoded_line.strip(),
    ]
    return None, reject


# 💡 [UPDATED] Iceberg 적재 함수 - 스키마 클리닝 + source_zip 추적 + 파티션 진화 탑재 완료
def write_to_iceberg(chunk_data, header, table_name, file_type, source_zip):
    if not chunk_data:
        return

    # 원본 헤더(지뢰 포함)대로 1차 데이터프레임 생성
    schema = StructType([StructField(col, StringType(), True) for col in header])
    df = spark.createDataFrame(chunk_data, schema)

    # 어느 zip에서 왔는지 남겨서, 재시작 시 이 zip 분량만 정확히 지우고 다시 채울 수 있게 함
    df = df.withColumn("source_zip", lit(source_zip))

    # 마법의 클리닝 함수 적용 (BOM, 공백 싹 청소하고 빈 기둥 세우기)
    df = harmonize_legacy_schema(df, file_type)

    df.createOrReplaceTempView("temp_chunk")

    ensure_partitioned_by_source_zip(spark, table_name)

    if not spark.catalog.tableExists(table_name):
        print(f"      -> 🏗️ [{table_name}] 최초 생성 및 적재 완료!")
        df.write.format("iceberg").saveAsTable(table_name)
        ensure_partitioned_by_source_zip(spark, table_name)
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
        # 1:N 관계인 약물, 부작용 정보는 무조건 밀어넣기(Append)
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

    # 1. AWS 열쇠 장착
    load_dotenv()
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

    # 2. Spark 세션 시작
    print("⏳ Spark 세션을 시작합니다...")
    spark = SparkSession.builder \
        .appName("FAERS_StageA_Raw_Pipeline") \
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

    # 3. 네임스페이스 생성
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS my_catalog.{NAMESPACE}")

    # 5. faers_로 시작하는 모든 ZIP 파일 연속 파싱 작전!
    zip_files = sorted(glob.glob("data/faers_not_load/faers_*.zip"), reverse=True)
    print(f"\n📦 총 {len(zip_files)}개의 ZIP 파일을 발견했습니다. 파이프라인 가동을 시작합니다!\n" + "=" * 60)

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

                # 재시작 안전장치: 이미 끝난 조합은 건너뛰고, 중간에 멈춘 조합은 부분 적재분부터 지운다
                status = manifest_status(spark, NAMESPACE, "faers", file_name, file_type)
                if status == "complete":
                    print(f"  ⏭️ [{file_name}] {file_type} 이미 완료됨(load_manifest) — 건너뜁니다.")
                    continue
                if status == "in_progress":
                    print(f"  🔧 [{file_name}] {file_type} 중간에 멈춘 기록 발견 — 부분 적재분 정리 후 재처리합니다.")
                    delete_partial_rows(spark, table_name, file_name)
                    delete_partial_rows(spark, REJECTS_TABLE, file_name)

                upsert_manifest(spark, NAMESPACE, "faers", file_name, file_type, "in_progress")

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

                upsert_manifest(spark, NAMESPACE, "faers", file_name, file_type, "complete",
                                 row_count=total_count, rejected_count=total_rejected)
                print(f"  ✅ [{file_type}] 총 {total_count}건 처리 완료! (컬럼 불일치로 격리된 레코드 {total_rejected}건 -> {REJECTS_TABLE})")

    print("\n🎉 모든 ZIP 파일의 [DEMO, DRUG, REAC] 분리 적재 파이프라인이 완벽하게 종료되었습니다!")


if __name__ == "__main__":
    run()