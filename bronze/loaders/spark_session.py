"""S3(Iceberg) 접근용 Spark 세션을 만드는 공용 함수.

스크립트마다 복붙돼 있던 세션 설정을 한 곳으로 모으고, 인증 방식을 환경에 맞게 고른다.
  - AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY 가 있으면 그 키를 쓴다 (로컬, bronze/.env).
    AWS_SESSION_TOKEN 도 있으면 임시 자격증명으로 인증한다.
  - 없으면 AWS 기본 자격증명 체인을 쓴다 (EC2 인스턴스 역할 — 서버에 키를 두지 않는다).

bronze/CLAUDE.md 조회 비용 규칙은 그대로 적용된다. 이 함수는 세션만 만들 뿐 조회 범위를 바꾸지 않는다.
"""
import os
from pathlib import Path

BUCKET_NAME = "boaz-1234-825494477740-ap-northeast-2-an"
DEFAULT_WAREHOUSE = f"s3a://{BUCKET_NAME}/iceberg_warehouse"
S3_ENDPOINT = "s3.ap-northeast-2.amazonaws.com"
PACKAGES = (
    "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0,"
    "org.apache.hadoop:hadoop-aws:3.3.4,"
    "com.amazonaws:aws-java-sdk-bundle:1.12.262"
)

_PROVIDER_KEY = "spark.hadoop.fs.s3a.aws.credentials.provider"
_KEY_PROVIDER = "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider"
_CHAIN_PROVIDER = "com.amazonaws.auth.DefaultAWSCredentialsProviderChain"
_TEMP_PROVIDER = "org.apache.hadoop.fs.s3a.TemporaryAWSCredentialsProvider"


def s3a_credentials_conf(env=None):
    """환경변수를 보고 S3A 인증 설정(dict)을 돌려준다. Spark를 띄우지 않는 순수 함수."""
    env = os.environ if env is None else env
    key = (env.get("AWS_ACCESS_KEY_ID") or "").strip()
    secret = (env.get("AWS_SECRET_ACCESS_KEY") or "").strip()

    token = (env.get("AWS_SESSION_TOKEN") or "").strip()

    if key and secret:
        conf = {
            "spark.hadoop.fs.s3a.access.key": key,
            "spark.hadoop.fs.s3a.secret.key": secret,
            _PROVIDER_KEY: _KEY_PROVIDER,
        }
        if token:  # SSO/STS 임시 자격증명은 세션 토큰이 함께 있어야 인증된다
            conf["spark.hadoop.fs.s3a.session.token"] = token
            conf[_PROVIDER_KEY] = _TEMP_PROVIDER
        return conf
    if key or secret:
        # 한쪽만 있으면 조용히 역할로 넘어가지 않고 바로 알려준다 (잘못된 .env 방지)
        raise ValueError("AWS_ACCESS_KEY_ID 와 AWS_SECRET_ACCESS_KEY 는 둘 다 있거나 둘 다 없어야 합니다.")
    return {_PROVIDER_KEY: _CHAIN_PROVIDER}


def _load_env_file():
    """bronze/.env 가 있으면 읽는다. EC2처럼 파일이 없거나 dotenv 미설치여도 조용히 넘어간다."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(Path(__file__).resolve().parent / ".env")


def build_spark(app_name, driver_memory="4g", master=None, shuffle_partitions=None,
                warehouse=DEFAULT_WAREHOUSE, extra_conf=None):
    from pyspark.sql import SparkSession

    _load_env_file()

    builder = (
        SparkSession.builder.appName(app_name)
        .config("spark.driver.memory", driver_memory)
        .config("spark.jars.packages", PACKAGES)
        .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
        .config("spark.sql.catalog.my_catalog", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.my_catalog.type", "hadoop")
        .config("spark.sql.catalog.my_catalog.warehouse", warehouse)
        .config("spark.hadoop.fs.s3a.endpoint", S3_ENDPOINT)
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
    )
    if master:
        builder = builder.master(master)
    if shuffle_partitions:
        builder = builder.config("spark.sql.shuffle.partitions", str(shuffle_partitions))
    for k, v in s3a_credentials_conf().items():
        builder = builder.config(k, v)
    for k, v in (extra_conf or {}).items():
        builder = builder.config(k, v)
    return builder.getOrCreate()
