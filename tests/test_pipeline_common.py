"""
bronze/pipeline_common.py의 재시작 안전장치 검증:
- load_manifest로 (zip, file_type) 단위 완료 여부 추적 -> 재실행 시 자동 스킵/복구
- source_zip 컬럼 + delete_partial_rows로, 중간에 멈춘 append 테이블도 중복 없이 재처리 가능

전부 로컬 Iceberg 카탈로그로 돌며 S3는 건드리지 않는다.
"""

from conftest import load_module


def get_pipeline_common():
    return load_module("pipeline_common", "bronze/pipeline_common.py")


def test_manifest_status_is_none_when_table_does_not_exist(local_iceberg_spark):
    pc = get_pipeline_common()
    status = pc.manifest_status(local_iceberg_spark, "test_bronze", "faers", "no_such.zip", "DEMO")
    assert status is None


def test_upsert_then_read_manifest_status(local_iceberg_spark):
    pc = get_pipeline_common()
    pc.upsert_manifest(local_iceberg_spark, "test_bronze", "faers", "faers_ascii_2013q9.zip", "DRUG", "in_progress")

    status = pc.manifest_status(local_iceberg_spark, "test_bronze", "faers", "faers_ascii_2013q9.zip", "DRUG")
    assert status == "in_progress"

    # 다른 (zip, file_type) 조합은 여전히 기록 없음
    assert pc.manifest_status(local_iceberg_spark, "test_bronze", "faers", "faers_ascii_2013q9.zip", "REAC") is None


def test_upsert_manifest_updates_in_place_not_duplicate(local_iceberg_spark):
    pc = get_pipeline_common()
    key = ("faers", "faers_ascii_2013q8.zip", "REAC")

    pc.upsert_manifest(local_iceberg_spark, "test_bronze", *key, "in_progress")
    pc.upsert_manifest(local_iceberg_spark, "test_bronze", *key, "complete", row_count=544835, rejected_count=0)

    assert pc.manifest_status(local_iceberg_spark, "test_bronze", *key) == "complete"

    rows = local_iceberg_spark.sql(f"""
        SELECT * FROM {pc.manifest_table("test_bronze")}
        WHERE pipeline = 'faers' AND source_zip = 'faers_ascii_2013q8.zip' AND file_type = 'REAC'
    """).collect()
    assert len(rows) == 1  # 새 행이 추가되는 게 아니라 같은 행이 갱신되어야 한다
    assert rows[0].row_count == 544835


def test_ensure_partitioned_by_source_zip_is_idempotent_and_safe_on_missing_table(local_iceberg_spark):
    pc = get_pipeline_common()
    # 테이블 없을 때: 에러 없이 그냥 넘어가야 함
    pc.ensure_partitioned_by_source_zip(local_iceberg_spark, "my_catalog.test_bronze.no_such_table")

    table = "my_catalog.test_bronze.partition_evolution_test"
    local_iceberg_spark.createDataFrame([("a", "z1.zip")], ["val", "source_zip"]) \
        .write.format("iceberg").saveAsTable(table)

    # 두 번 호출해도 에러 없이 통과해야 함 (이미 파티션 필드로 추가된 경우를 처리)
    pc.ensure_partitioned_by_source_zip(local_iceberg_spark, table)
    pc.ensure_partitioned_by_source_zip(local_iceberg_spark, table)


def test_delete_partial_rows_removes_only_matching_zip(local_iceberg_spark):
    pc = get_pipeline_common()
    table = "my_catalog.test_bronze.delete_partial_test"
    local_iceberg_spark.createDataFrame(
        [("1", "faers_ascii_2013q2.zip"), ("2", "faers_ascii_2013q2.zip"), ("3", "faers_ascii_2013q1.zip")],
        ["id", "source_zip"],
    ).write.format("iceberg").saveAsTable(table)

    pc.delete_partial_rows(local_iceberg_spark, table, "faers_ascii_2013q2.zip")

    remaining = local_iceberg_spark.table(table).collect()
    assert sorted(r.id for r in remaining) == ["3"]

    # 테이블 없을 때도 에러 없이 넘어가야 함
    pc.delete_partial_rows(local_iceberg_spark, "my_catalog.test_bronze.no_such_table", "x.zip")


def test_crash_then_restart_does_not_duplicate_append_only_table(local_iceberg_spark):
    """
    이번에 실제로 겪었던 시나리오 재현: DRUG(append 전용) 처리 중 청크 하나만 쓰고 크래시
    (manifest는 'in_progress'로 남음) -> 재시작 시 이 zip 분량을 지우고 처음부터 다시 채움
    -> 중복 없이 정확한 건수만 남아야 한다.
    """
    pc = get_pipeline_common()
    table = "my_catalog.test_bronze.faers_drug_crash_test"
    source_zip = "faers_ascii_2013q2.zip"

    # 1차 시도: 청크 1개(2건)만 쓰고 크래시 (manifest 'complete' 마킹 전에 죽었다고 가정)
    partial_chunk = local_iceberg_spark.createDataFrame(
        [("1", "ASPIRIN", source_zip), ("2", "IBUPROFEN", source_zip)],
        ["primaryid", "drugname", "source_zip"],
    )
    partial_chunk.write.format("iceberg").saveAsTable(table)
    pc.upsert_manifest(local_iceberg_spark, "test_bronze", "faers", source_zip, "DRUG", "in_progress")

    # 재시작: manifest가 in_progress면 재처리 전에 이 zip 분량부터 지운다
    status = pc.manifest_status(local_iceberg_spark, "test_bronze", "faers", source_zip, "DRUG")
    assert status == "in_progress"
    pc.delete_partial_rows(local_iceberg_spark, table, source_zip)

    assert local_iceberg_spark.table(table).count() == 0  # 부분 적재분 깨끗이 제거됨

    # 이번엔 파일 전체(4건)를 처음부터 끝까지 다시 채움
    full_chunk = local_iceberg_spark.createDataFrame(
        [("1", "ASPIRIN", source_zip), ("2", "IBUPROFEN", source_zip),
         ("3", "TYLENOL", source_zip), ("4", "ADVIL", source_zip)],
        ["primaryid", "drugname", "source_zip"],
    )
    full_chunk.write.format("iceberg").mode("append").saveAsTable(table)
    pc.upsert_manifest(local_iceberg_spark, "test_bronze", "faers", source_zip, "DRUG", "complete", row_count=4)

    final_rows = local_iceberg_spark.table(table).collect()
    assert len(final_rows) == 4  # 중복 없음 (예전엔 2 + 4 = 6건으로 중복됐을 상황)
    assert pc.manifest_status(local_iceberg_spark, "test_bronze", "faers", source_zip, "DRUG") == "complete"
