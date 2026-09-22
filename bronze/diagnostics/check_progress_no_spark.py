# Spark/쿼리 없이, boto3로 S3의 Iceberg 메타데이터 JSON 파일 하나만 직접 읽어서
# 진행상황을 확인합니다. 지금 실행 중인 적재 작업과 리소스 경쟁도 없고,
# GET 요청 1~2번뿐이라 사실상 비용이 0에 가깝습니다.

import sys
import os
import json
import boto3
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
BUCKET = "boaz-1234-825494477740-ap-northeast-2-an"
NAMESPACE = "stage_a_raw"

s3 = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name="ap-northeast-2",
)


def latest_metadata_json(table):
    prefix = f"iceberg_warehouse/{NAMESPACE}/{table}/metadata/"
    hint_key = prefix + "version-hint.text"
    try:
        version = s3.get_object(Bucket=BUCKET, Key=hint_key)["Body"].read().decode().strip()
        return prefix + f"v{version}.metadata.json"
    except s3.exceptions.NoSuchKey:
        return None


def show_progress(table, n=5):
    key = latest_metadata_json(table)
    print(f"\n{'=' * 70}\n📊 {table}\n{'=' * 70}")
    if key is None:
        print("   ⚠️ 테이블 없음 (아직 적재 전)")
        return

    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    meta = json.loads(body)

    snapshots = sorted(meta.get("snapshots", []), key=lambda s: s["timestamp-ms"], reverse=True)[:n]
    for snap in snapshots:
        import datetime
        ts = datetime.datetime.fromtimestamp(snap["timestamp-ms"] / 1000)
        summary = snap.get("summary", {})
        print(f"   {ts}  {summary.get('operation', '?'):9s}  +{summary.get('added-records', '?')}건"
              f"  (total-records={summary.get('total-records', '?')})")


for t in ["faers_demo", "faers_drug", "faers_reac", "faers_load_rejects",
          "aers_demo", "aers_drug", "aers_reac", "aers_load_rejects"]:
    show_progress(t)

print("\n✅ 확인 완료 (Spark 세션 없이 boto3 GET 요청 몇 번뿐).")
