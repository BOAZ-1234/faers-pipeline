"""
openFDA bulk download 공통 유틸 — drug/label, drug/ndc가 공유
api.fda.gov/download.json에서 파티션 zip 목록을 받아 하나씩 내려받아 파싱한다.
skip 상한(25000) 없이 전량을 커버할 수 있는 방식.
"""
import io
import json
import time
import urllib.request
import zipfile

INDEX_URL = "https://api.fda.gov/download.json"


def get_partition_urls(category: str, endpoint: str) -> list[str]:
    """category='drug', endpoint='label'|'ndc' → 파티션 zip URL 목록"""
    with urllib.request.urlopen(INDEX_URL, timeout=15) as r:
        index = json.loads(r.read())
    part = index["results"][category][endpoint]
    return [p["file"] for p in part["partitions"]], part["total_records"]


def fetch_records(url: str, retries: int = 3) -> list[dict]:
    """zip 파일 하나를 받아 그 안의 JSON을 파싱해 results 리스트를 반환"""
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                zip_bytes = r.read()
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                name = zf.namelist()[0]
                with zf.open(name) as f:
                    data = json.load(f)
            return data.get("results", [])
        except Exception as e:
            wait = 2 ** attempt
            print(f"  [{url}] 실패({e}) — {wait}s 후 재시도 ({attempt + 1}/{retries})", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"{url} 다운로드가 {retries}번 모두 실패")
