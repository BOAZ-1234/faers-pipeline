# check_faers_expected_counts.py / find_missing_quarters.py로 찾아낸 두 구멍을 메운다.
#   - 2013Q3: DEMO/DRUG/REAC 전부 라이브에 0건 -> 그냥 안전하게 추가 적재
#   - 2013Q4: REAC만 112,932건 부분 유실 -> 기존 2013Q4 REAC 행을 지우고 zip 전체를 다시 append
#             (DEMO/DRUG는 2013Q4에 이미 완전하므로 안 건드림)
#
# load_faers_master.py의 classify_line/harmonize_legacy_schema를 그대로 import해서 재사용한다
# (같은 파싱 로직으로 짜야 원본 로더가 만들었을 결과와 100% 동일한 행이 나온다).
# 단, write_to_iceberg()는 재사용하지 않는다 — 그 함수는 source_zip 컬럼이 테이블에 있다고
# 가정하는데, 지금 faers_demo/drug/reac에는 source_zip 컬럼 자체가 없다(그 자체가 지난번에
# 찾은 별도 문제, 이번 백필의 범위 밖). 그래서 harmonize까지만 재사용하고 source_zip 컬럼은
# 쓰기 직전에 드롭해서 기존 테이블 스키마와 정확히 맞춘다.

import sys
import os
import zipfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from load_faers_master import classify_line, harmonize_legacy_schema  # noqa: E402
from pipeline_common import manifest_status, upsert_manifest  # noqa: E402

from dotenv import load_dotenv
from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType, IntegerType
from pyspark.sql.functions import lit

load_dotenv("bronze/.env")
aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"
NAMESPACE = "stage_a_raw"

ZIP_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "faers_load")
TABLE = {
    "DEMO": f"my_catalog.{NAMESPACE}.faers_demo",
    "DRUG": f"my_catalog.{NAMESPACE}.faers_drug",
    "REAC": f"my_catalog.{NAMESPACE}.faers_reac",
}
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

spark = SparkSession.builder \
    .appName("Backfill_2013_Gaps") \
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


def parse_zip_filetype(zip_path, file_type):
    """classify_line 그대로 써서 (정상행 리스트, header, reject 리스트)를 반환."""
    file_name = os.path.basename(zip_path)
    with zipfile.ZipFile(zip_path, "r") as z:
        target_file = [f for f in z.namelist() if file_type in f.upper() and f.upper().endswith(".TXT")]
        target = target_file[0]
        with z.open(target) as f:
            header = f.readline().decode("utf-8").strip().split("$")
            rows, rejects = [], []
            line_no = 1
            for raw_line in f:
                line_no += 1
                row, reject = classify_line(raw_line, header, file_name, target, file_type, line_no)
                if row is not None:
                    rows.append(row)
                else:
                    rejects.append(reject)
    return rows, header, rejects


def build_harmonized_df(rows, header, file_type, source_zip_tag):
    """harmonize_legacy_schema는 source_zip 컬럼을 요구하므로 태그를 붙였다가,
    기존 라이브 테이블(source_zip 컬럼 없음)에 맞춰 쓰기 직전에 다시 뺀다."""
    schema = StructType([StructField(c, StringType(), True) for c in header])
    df = spark.createDataFrame(rows, schema)
    df = df.withColumn("source_zip", lit(source_zip_tag))
    df = harmonize_legacy_schema(df, file_type)
    df = df.drop("source_zip")  # 라이브 테이블 스키마와 정확히 일치시킴 (이번 백필 범위 밖 이슈)
    return df


def write_rejects(rejects, source_zip):
    if not rejects:
        return 0
    df = spark.createDataFrame(rejects, REJECTS_SCHEMA)
    if not spark.catalog.tableExists(REJECTS_TABLE):
        df.write.format("iceberg").saveAsTable(REJECTS_TABLE)
    else:
        df.write.format("iceberg").mode("append").saveAsTable(REJECTS_TABLE)
    return len(rejects)


def already_done(source_zip, file_type):
    status = manifest_status(spark, NAMESPACE, "faers", source_zip, file_type)
    if status == "complete":
        print(f"  ⏭️ [{source_zip}] {file_type} 이미 complete로 기록됨 — 스킵")
        return True
    return False


# ── 1) 2013Q3: DEMO/DRUG/REAC 전부 신규 적재 ──────────────────────────────
Q3_ZIP = os.path.join(ZIP_DIR, "faers_ascii_2013q3.zip")
Q3_NAME = "faers_ascii_2013q3.zip"
print(f"\n{'='*70}\n2013Q3 백필 시작 ({Q3_NAME})\n{'='*70}")

for ft in ["DEMO", "DRUG", "REAC"]:
    if already_done(Q3_NAME, ft):
        continue
    print(f"\n[{ft}] 파싱 중...")
    rows, header, rejects = parse_zip_filetype(Q3_ZIP, ft)
    print(f"  파싱 완료: 정상 {len(rows):,}행, 격리 {len(rejects):,}행")

    df = build_harmonized_df(rows, header, ft, Q3_NAME)
    table = TABLE[ft]

    if ft == "DEMO":
        df.createOrReplaceTempView("temp_backfill_q3")
        spark.sql(f"""
            MERGE INTO {table} t
            USING temp_backfill_q3 s
            ON t.primaryid = s.primaryid AND t.caseversion = s.caseversion
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
        """)
    else:
        df.write.format("iceberg").mode("append").saveAsTable(table)

    rej_count = write_rejects(rejects, Q3_NAME)
    upsert_manifest(spark, NAMESPACE, "faers", Q3_NAME, ft, "complete",
                     row_count=len(rows), rejected_count=rej_count)
    print(f"  ✅ [{ft}] {len(rows):,}행 적재 완료 (격리 {rej_count}건), load_manifest 기록함")

# ── 2) 2013Q4: REAC만 삭제 후 재적재 ──────────────────────────────────────
Q4_ZIP = os.path.join(ZIP_DIR, "faers_ascii_2013q4.zip")
Q4_NAME = "faers_ascii_2013q4.zip"
print(f"\n{'='*70}\n2013Q4 REAC 백필 시작 ({Q4_NAME}) — 기존 행 삭제 후 재적재\n{'='*70}")

if already_done(Q4_NAME, "REAC"):
    pass
else:
    rows, header, rejects = parse_zip_filetype(Q4_ZIP, "REAC")
    print(f"  파싱 완료: 정상 {len(rows):,}행, 격리 {len(rejects):,}행")

    df = build_harmonized_df(rows, header, "REAC", Q4_NAME)
    table = TABLE["REAC"]

    # 이 zip 소속 primaryid만 골라 기존 부분 적재분을 지운다 (source_zip 컬럼이 없어서
    # 이번에 새로 파싱한 REAC의 primaryid 집합을 삭제 키로 쓴다 — 지우고 나서 바로
    # 이 zip 전체를 다시 채우니 두 primaryid 집합은 정확히 동일함)
    pid_view = df.select("primaryid").distinct()
    pid_view.createOrReplaceTempView("q4_reac_primaryids")

    before_count = spark.table(table).count()
    spark.sql(f"""
        DELETE FROM {table}
        WHERE primaryid IN (SELECT primaryid FROM q4_reac_primaryids)
    """)
    after_delete_count = spark.table(table).count()
    print(f"  🗑️ 삭제: {before_count:,} -> {after_delete_count:,} (제거 {before_count - after_delete_count:,}행)")

    df.write.format("iceberg").mode("append").saveAsTable(table)
    after_append_count = spark.table(table).count()
    print(f"  ➕ 재적재: {after_delete_count:,} -> {after_append_count:,} (추가 {after_append_count - after_delete_count:,}행)")

    rej_count = write_rejects(rejects, Q4_NAME)
    upsert_manifest(spark, NAMESPACE, "faers", Q4_NAME, "REAC", "complete",
                     row_count=len(rows), rejected_count=rej_count)
    print(f"  ✅ [REAC] {len(rows):,}행 재적재 완료 (격리 {rej_count}건), load_manifest 기록함")

print("\n🎉 백필 완료.")
spark.stop()
