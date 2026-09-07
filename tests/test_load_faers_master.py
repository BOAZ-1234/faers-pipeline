"""
bronze/load_faers_master.py의 4-1 요구사항(컬럼 불일치 레코드를 버리지 않고
격리 테이블에 적재) 검증 테스트.

- test_classify_line_*: 순수 로직 (Spark 불필요, 빠름)
- test_reject_records_*: 실제 write_to_iceberg / write_rejects_to_iceberg를
  로컬 Iceberg 카탈로그(S3 아님)에 대고 돌려서 진짜 적재 경로까지 확인
"""

from conftest import load_module


def get_bronze_module():
    return load_module("load_faers_master", "bronze/load_faers_master.py")


def test_classify_line_passes_through_well_formed_rows():
    mod = get_bronze_module()
    header = ["ID", "NAME", "VALUE"]

    row, reject = mod.classify_line(b"1$aspirin$100\n", header, "f.zip", "DRUG.txt", "DRUG", 2)

    assert row == ["1", "aspirin", "100"]
    assert reject is None


def test_classify_line_quarantines_short_rows_instead_of_dropping():
    mod = get_bronze_module()
    header = ["ID", "NAME", "VALUE"]

    row, reject = mod.classify_line(b"2$ibuprofen\n", header, "f.zip", "DRUG.txt", "DRUG", 3)

    assert row is None
    assert reject == ["f.zip", "DRUG.txt", "DRUG", 3, 3, 2, "2$ibuprofen"]


def test_classify_line_quarantines_long_rows_instead_of_dropping():
    mod = get_bronze_module()
    header = ["ID", "NAME", "VALUE"]

    row, reject = mod.classify_line(b"3$tylenol$200$extra\n", header, "f.zip", "DRUG.txt", "DRUG", 4)

    assert row is None
    assert reject == ["f.zip", "DRUG.txt", "DRUG", 4, 3, 4, "3$tylenol$200$extra"]


def test_reject_records_land_in_quarantine_table_via_pyspark(local_iceberg_spark):
    mod = get_bronze_module()
    mod.spark = local_iceberg_spark  # 운영 S3 세션 대신 로컬 테스트 세션으로 교체
    mod.REJECTS_TABLE = "my_catalog.test_bronze.faers_load_rejects_test"

    header = ["primaryid", "drugname", "route"]
    lines = [
        b"1$ASPIRIN$ORAL\n",       # 정상
        b"2$IBUPROFEN\n",          # 격리 대상: 컬럼 1개 부족
        b"3$TYLENOL$ORAL$EXTRA\n",  # 격리 대상: 컬럼 1개 초과
        b"4$ADVIL$ORAL\n",         # 정상
    ]

    chunk, reject_chunk = [], []
    for line_no, line in enumerate(lines, start=2):
        row, reject = mod.classify_line(line, header, "TEST25Q1.zip", "DRUG25Q1.txt", "DRUG", line_no)
        (chunk if row is not None else reject_chunk).append(row if row is not None else reject)

    assert len(chunk) == 2
    assert len(reject_chunk) == 2

    good_table = "my_catalog.test_bronze.faers_drug_test"
    mod.write_to_iceberg(chunk, header, good_table, "DRUG")
    mod.write_rejects_to_iceberg(reject_chunk)

    written = local_iceberg_spark.table(good_table).orderBy("primaryid").collect()
    assert [r.primaryid for r in written] == ["1", "4"]

    rejects = local_iceberg_spark.table(mod.REJECTS_TABLE).orderBy("line_no").collect()
    assert [r.line_no for r in rejects] == [3, 4]
    assert rejects[0].expected_cols == 3 and rejects[0].actual_cols == 2
    assert rejects[0].raw_line == "2$IBUPROFEN"
    assert rejects[1].expected_cols == 3 and rejects[1].actual_cols == 4
    assert rejects[1].raw_line == "3$TYLENOL$ORAL$EXTRA"
