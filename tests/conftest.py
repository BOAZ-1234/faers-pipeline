import importlib.util
import pathlib

import pytest
from pyspark.sql import SparkSession

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module(module_name, relative_path):
    """bronze/*.py는 패키지가 아니라 스크립트라서 importlib으로 직접 로드한다."""
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def local_iceberg_spark(tmp_path_factory):
    """
    S3를 전혀 건드리지 않는 로컬 파일시스템 기반 Iceberg 카탈로그.
    bronze/CLAUDE.md 규칙(조회 비용 절감)에 맞춰 테스트는 실제 S3 버킷에 접근하지 않는다.
    """
    warehouse_dir = tmp_path_factory.mktemp("iceberg_warehouse")
    warehouse_uri = warehouse_dir.as_posix()

    spark = (
        SparkSession.builder
        .appName("bronze_unit_tests")
        .master("local[1]")
        .config("spark.jars.packages", "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0")
        .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
        .config("spark.sql.catalog.my_catalog", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.my_catalog.type", "hadoop")
        .config("spark.sql.catalog.my_catalog.warehouse", warehouse_uri)
        .getOrCreate()
    )
    spark.sql("CREATE NAMESPACE IF NOT EXISTS my_catalog.test_bronze")
    yield spark
    spark.stop()
