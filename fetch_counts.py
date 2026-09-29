"""FAERS drugname 빈도 뽑기 → counts.csv (diana_golden.py --freq-csv 용)"""
import csv
import duckdb
from pathlib import Path

BUCKET = "boaz-1234-825494477740-ap-northeast-2-an"
FAERS_DRUG_TABLE = f"s3://{BUCKET}/iceberg_warehouse/stage_a_raw/faers_drug"
OUT = Path("counts.csv")

con = duckdb.connect()
con.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
con.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain, REGION 'ap-northeast-2');")
con.execute("SET unsafe_enable_version_guessing = true;")

print("S3 스캔 중... (수 분 걸림)", flush=True)
rows = con.execute(f"""
    SELECT upper(trim(drugname)) AS name, count(*) AS n_reports
    FROM iceberg_scan('{FAERS_DRUG_TABLE}')
    WHERE drugname IS NOT NULL AND trim(drugname) != ''
    GROUP BY 1
""").fetchall()

with open(OUT, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["name", "n_reports"])
    w.writerows(rows)

print(f"완료: {len(rows):,}개 → {OUT}")
