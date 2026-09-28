# FAERS/AERS 적재 스크립트가 공유하는 재시작 안전장치입니다.
#
# 문제였던 것: DEMO는 MERGE라 재실행해도 안전한데, DRUG/REAC는 무조건 append라
# 크래시 후 재실행하면 이미 들어간 분량이 중복된다. 게다가 실제 데이터에 "어느 zip에서
# 왔는지"가 안 남아있어서, 크래시 후 어디까지 됐는지 스냅샷 메타데이터를 역산해야 했다.
#
# 여기서 하는 일 두 가지:
#   1. load_manifest 테이블에 (pipeline, source_zip, file_type) 단위로 완료 여부를 기록해서,
#      재실행 시 이미 끝난 조합은 자동으로 건너뛰고, 중간에 멈춘 조합은 자동으로 감지한다.
#   2. 모든 데이터 테이블에 source_zip 컬럼을 남겨서, 중간에 멈춘 조합을 재처리하기 전에
#      "이 zip에서 이미 들어간 행"만 정확히 지우고 다시 채울 수 있게 한다 (source_zip 파티션
#      진화 덕분에 이 삭제는 그 zip의 파티션만 건드리는 저렴한 작업이다).

MANIFEST_TABLE_NAME = "load_manifest"


def manifest_table(namespace):
    return f"my_catalog.{namespace}.{MANIFEST_TABLE_NAME}"


def _manifest_schema():
    from pyspark.sql.types import StructType, StructField, StringType, IntegerType
    return StructType([
        StructField("pipeline", StringType(), True),
        StructField("source_zip", StringType(), True),
        StructField("file_type", StringType(), True),
        StructField("status", StringType(), True),
        StructField("row_count", IntegerType(), True),
        StructField("rejected_count", IntegerType(), True),
    ])


def manifest_status(spark, namespace, pipeline, source_zip, file_type):
    """이 조합이 'complete'인지 'in_progress'(중간에 멈춤)인지, 처음이면 None을 반환한다."""
    table = manifest_table(namespace)
    if not spark.catalog.tableExists(table):
        return None
    rows = spark.sql(f"""
        SELECT status FROM {table}
        WHERE pipeline = '{pipeline}' AND source_zip = '{source_zip}' AND file_type = '{file_type}'
    """).collect()
    return rows[0]["status"] if rows else None


def upsert_manifest(spark, namespace, pipeline, source_zip, file_type, status, row_count=None, rejected_count=None):
    table = manifest_table(namespace)
    row = [(pipeline, source_zip, file_type, status, row_count, rejected_count)]
    df = spark.createDataFrame(row, _manifest_schema())
    df.createOrReplaceTempView("temp_manifest_row")

    if not spark.catalog.tableExists(table):
        df.write.format("iceberg").saveAsTable(table)
        return

    spark.sql(f"""
        MERGE INTO {table} t
        USING temp_manifest_row s
        ON t.pipeline = s.pipeline AND t.source_zip = s.source_zip AND t.file_type = s.file_type
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
    """)


def ensure_partitioned_by_source_zip(spark, table_name):
    """source_zip으로 파티션 진화. 기존 데이터는 안 건드리고, 앞으로 쓰는 데이터부터 적용된다."""
    if not spark.catalog.tableExists(table_name):
        return
    try:
        spark.sql(f"ALTER TABLE {table_name} ADD PARTITION FIELD source_zip")
    except Exception as e:
        msg = str(e).lower()
        if "already" not in msg and "duplicate" not in msg and "exists" not in msg:
            print(f"      ⚠️ 파티션 진화 스킵 ({table_name}): {e}")


def delete_partial_rows(spark, table_name, source_zip):
    """재시도 전에, 이 zip에서 이미 부분적으로 들어간 행을 지운다 (append 테이블 중복 방지)."""
    if not spark.catalog.tableExists(table_name):
        return
    spark.sql(f"DELETE FROM {table_name} WHERE source_zip = '{source_zip}'")
