"""
bronze/load_faers_master.py의 4-1 요구사항(컬럼 불일치 레코드를 버리지 않고
격리 테이블에 적재) 검증 테스트.

- test_classify_line_*: 순수 로직 (Spark 불필요, 빠름)
- test_reject_records_*: 실제 write_to_iceberg / write_rejects_to_iceberg를
  로컬 Iceberg 카탈로그(S3 아님)에 대고 돌려서 진짜 적재 경로까지 확인
"""

from conftest import load_module


def get_bronze_module():
    return load_module("load_faers_master", "bronze/loaders/load_faers_master.py")


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
    mod.write_to_iceberg(chunk, header, good_table, "DRUG", "TEST25Q1.zip")
    mod.write_rejects_to_iceberg(reject_chunk)

    written = local_iceberg_spark.table(good_table).orderBy("primaryid").collect()
    assert [r.primaryid for r in written] == ["1", "4"]

    rejects = local_iceberg_spark.table(mod.REJECTS_TABLE).orderBy("line_no").collect()
    assert [r.line_no for r in rejects] == [3, 4]
    assert rejects[0].expected_cols == 3 and rejects[0].actual_cols == 2
    assert rejects[0].raw_line == "2$IBUPROFEN"
    assert rejects[1].expected_cols == 3 and rejects[1].actual_cols == 4
    assert rejects[1].raw_line == "3$TYLENOL$ORAL$EXTRA"


# --- OUTC 적재 (#77) ----------------------------------------------------------

def test_parse_args_defaults_keep_previous_behavior():
    # 인자 없이 돌리면 예전과 같은 세 종류·같은 폴더 — OUTC가 몰래 끼어들지 않는다
    mod = get_bronze_module()
    types, zip_dir, chunk_size = mod.parse_args([])
    assert types == ["DEMO", "DRUG", "REAC"]
    assert zip_dir == "data/faers_not_load"
    assert chunk_size == mod.CHUNK_SIZE


def test_parse_args_selects_only_requested_types():
    # OUTC만 지정하면 DRUG/REAC는 대상에서 빠진다(소급 manifest 전 재적재 시 중복 방지)
    mod = get_bronze_module()
    types, zip_dir, chunk_size = mod.parse_args(["--types", "outc", "--zip-dir", "data/faers_load",
                                                 "--chunk-size", "200000"])
    assert types == ["OUTC"]
    assert zip_dir == "data/faers_load"
    assert chunk_size == 200000


def test_parse_args_rejects_unknown_type():
    import pytest
    mod = get_bronze_module()
    with pytest.raises(SystemExit):
        mod.parse_args(["--types", "OUTC,THER"])


def test_outc_legacy_header_is_harmonized_via_pyspark(local_iceberg_spark):
    # 2012Q4 OUTC만 헤더가 outc_code — outc_cod로 통일돼 최신 분기와 같은 테이블에 쌓여야 한다
    mod = get_bronze_module()
    mod.spark = local_iceberg_spark
    table = "my_catalog.test_bronze.faers_outc_test"

    mod.write_to_iceberg([["11", "1", "DE"]], ["primaryid", "caseid", "outc_code"], table, "OUTC",
                         "faers_ascii_2012q4.zip")
    mod.write_to_iceberg([["22", "2", "HO"], ["22", "2", "LT"]], ["primaryid", "caseid", "outc_cod"], table,
                         "OUTC", "faers_ascii_2024Q1.zip")

    df = local_iceberg_spark.table(table)
    assert df.columns == ["primaryid", "caseid", "outc_cod", "source_zip"]
    rows = sorted((r.primaryid, r.outc_cod, r.source_zip) for r in df.collect())
    assert rows == [("11", "DE", "faers_ascii_2012q4.zip"),
                    ("22", "HO", "faers_ascii_2024Q1.zip"),
                    ("22", "LT", "faers_ascii_2024Q1.zip")]
