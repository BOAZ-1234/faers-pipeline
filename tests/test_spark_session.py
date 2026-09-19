"""
bronze/spark_session.py 의 인증 방식 선택 검증.
Spark도 S3도 띄우지 않는다 (환경변수 -> 설정 dict 변환만 확인).
"""
import pytest
from conftest import load_module


def get_module():
    return load_module("spark_session", "bronze/spark_session.py")


PROVIDER = "spark.hadoop.fs.s3a.aws.credentials.provider"


def test_uses_keys_when_both_present():
    conf = get_module().s3a_credentials_conf({"AWS_ACCESS_KEY_ID": "AK", "AWS_SECRET_ACCESS_KEY": "SK"})
    assert conf["spark.hadoop.fs.s3a.access.key"] == "AK"
    assert conf["spark.hadoop.fs.s3a.secret.key"] == "SK"
    assert conf[PROVIDER].endswith("SimpleAWSCredentialsProvider")


def test_session_token_switches_to_temporary_provider():
    conf = get_module().s3a_credentials_conf(
        {"AWS_ACCESS_KEY_ID": "AK", "AWS_SECRET_ACCESS_KEY": "SK", "AWS_SESSION_TOKEN": "TOK"}
    )
    assert conf["spark.hadoop.fs.s3a.session.token"] == "TOK"
    assert conf[PROVIDER].endswith("TemporaryAWSCredentialsProvider")


def test_falls_back_to_credential_chain_without_keys():
    conf = get_module().s3a_credentials_conf({})
    assert conf == {PROVIDER: "com.amazonaws.auth.DefaultAWSCredentialsProviderChain"}


def test_blank_keys_count_as_missing():
    conf = get_module().s3a_credentials_conf({"AWS_ACCESS_KEY_ID": " ", "AWS_SECRET_ACCESS_KEY": ""})
    assert "spark.hadoop.fs.s3a.access.key" not in conf


@pytest.mark.parametrize("env", [
    {"AWS_ACCESS_KEY_ID": "AK"},
    {"AWS_SECRET_ACCESS_KEY": "SK"},
])
def test_half_configured_keys_raise(env):
    with pytest.raises(ValueError):
        get_module().s3a_credentials_conf(env)
