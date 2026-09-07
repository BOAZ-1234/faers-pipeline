# "지금 몇 분기까지 됐나"를 자동으로 계산해서 보여줍니다.
# - 로컬 zip 파일들의 정확한 DEMO/DRUG/REAC 줄 수를 미리 계산 (무료)
# - S3에서는 Iceberg 메타데이터 JSON만 boto3로 읽음 (Spark 없음, 쿼리 없음, 사실상 무료)
# - 오늘 커밋된 분량 누적 합계를 로컬 파일별 누적 기대값과 비교해서 "지금 이 분기 처리 중"을 추정

import sys
import os
import json
import glob
import zipfile
import datetime
import boto3
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
BUCKET = "boaz-1234-825494477740-ap-northeast-2-an"
NAMESPACE = "stage_a_raw"

# 처리 순서대로 (2014Q2가 가장 먼저 처리됨, 2012Q4가 마지막)
BACKFILL_QUARTERS = ["2014q2", "2014q1", "2013q4", "2013q3", "2013q2", "2013q1", "2012q4"]
BACKFILL_START = datetime.datetime(2026, 9, 3, 0, 0, 0)  # 이 시각 이후 커밋만 "이번 백필분"으로 집계

s3 = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name="ap-northeast-2",
)


def find_zip(quarter):
    for folder in ("data/faers_load", "data/faers_not_load"):
        matches = glob.glob(f"{folder}/faers_ascii_{quarter}.zip")
        if matches:
            return matches[0]
    return None


def count_lines(zip_path, file_type):
    with zipfile.ZipFile(zip_path) as z:
        target = [f for f in z.namelist() if file_type in f.upper() and f.upper().endswith(".TXT")][0]
        with z.open(target) as f:
            header = f.readline().decode("utf-8").strip().split("$")
            count = 0
            for line in f:
                data = line.decode("utf-8", errors="replace").strip().split("$")
                if len(data) == len(header):
                    count += 1
    return count


def backfill_committed_total(table):
    """오늘(BACKFILL_START 이후) 이 테이블에 실제로 커밋된 합계 (metadata.json만 읽음, 무료)"""
    prefix = f"iceberg_warehouse/{NAMESPACE}/{table}/metadata/"
    version = s3.get_object(Bucket=BUCKET, Key=prefix + "version-hint.text")["Body"].read().decode().strip()
    meta = json.loads(s3.get_object(Bucket=BUCKET, Key=prefix + f"v{version}.metadata.json")["Body"].read())

    total = 0
    for snap in meta.get("snapshots", []):
        ts = datetime.datetime.fromtimestamp(snap["timestamp-ms"] / 1000)
        if ts >= BACKFILL_START:
            total += int(snap.get("summary", {}).get("added-records", 0) or 0)
    return total


for file_type, table in [("DEMO", "faers_demo"), ("DRUG", "faers_drug"), ("REAC", "faers_reac")]:
    print(f"\n{'=' * 70}\n📊 {table}\n{'=' * 70}")

    actual = backfill_committed_total(table)
    print(f"   오늘 실제 커밋된 합계: {actual}건")

    cumulative = 0
    prev_cumulative = 0
    located = False
    for q in BACKFILL_QUARTERS:
        zip_path = find_zip(q)
        if zip_path is None:
            print(f"   ⚠️ {q} zip을 못 찾음 (faers_load/faers_not_load 둘 다 확인)")
            continue
        n = count_lines(zip_path, file_type)
        prev_cumulative = cumulative
        cumulative += n

        if not located and actual < cumulative:
            done_in_this_file = actual - prev_cumulative
            pct = done_in_this_file / n * 100
            print(f"   👉 현재 처리 중: {q} ({done_in_this_file}/{n}건, {pct:.1f}%)")
            located = True
        elif not located:
            print(f"   ✅ {q} 완료 ({n}건)")

    if not located:
        print("   🎉 백필 대상 7개 분기 전부 완료된 것으로 보입니다!")

print("\n✅ 확인 완료 (S3 데이터 파일은 전혀 읽지 않았습니다).")
