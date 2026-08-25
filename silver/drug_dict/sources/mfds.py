"""
C세부1 — 식약처 의약품 제품 허가정보에서 한글 제품명↔영문 성분명 쌍 추출
전체 42,960건 — API가 페이지당 최대 500건 지원해서 86페이지로 전량 커버 가능
(다른 3개 소스와 달리 부분(v0)이 아니라 전체 추출)

서비스키는 .env의 MFDS_SERVICE_KEY에서 읽는다 (레포에 커밋되지 않음).
"""
import csv
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent.parent  # faers-pipeline/
BASE_URL = "http://apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService07/getDrugPrdtPrmsnInq07"
PAGE_SIZE = 500  # API 상한
OUT_PATH = HERE / "output" / "mfds.csv"
CHECKPOINT_EVERY = 5


def load_service_key() -> str:
    env_path = ROOT / ".env"
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("MFDS_SERVICE_KEY="):
                return line.split("=", 1)[1]
    raise RuntimeError(f"{env_path}에 MFDS_SERVICE_KEY가 없음")


SERVICE_KEY = load_service_key()


def fetch_page(page_no: int, retries: int = 3) -> tuple[list[dict], int]:
    """실패하면 재시도. retries 다 소진하면 예외를 던진다."""
    params = {"serviceKey": SERVICE_KEY, "type": "json", "numOfRows": str(PAGE_SIZE), "pageNo": str(page_no)}
    url = BASE_URL + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                data = json.loads(r.read())
            body = data.get("body", {})
            return body.get("items", []), body.get("totalCount", 0)
        except Exception as e:
            wait = 2 ** attempt
            print(f"  [page={page_no}] 요청 실패({e}) — {wait}s 후 재시도 ({attempt + 1}/{retries})", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"page={page_no} 요청이 {retries}번 모두 실패")


def extract_pairs(item: dict) -> list[dict]:
    """한 허가 레코드에서 (한글 제품명, 영문 성분명) 쌍 추출 — drug_ingredient_map 스키마.
    UNII는 이 API에 없는 필드라 비워둔다 (openFDA 쪽에서 채워짐).

    ITEM_INGR_NAME은 "/"로 성분을 구분하는데, 원본 데이터 자체에 화학명 속 쉼표가
    "/"로 깨져 들어오는 경우가 있다 (예: "1,4-Butanediol..." → "1/4-Butanediol...").
    이걸 그대로 "/"로 쪼개면 "1", "4-Butanediol..." 같은 조각이 나온다. API가 주는
    ITEM_INGR_CNT(예상 성분 개수)와 쪼갠 개수가 다르면 오염 의심으로 보고 쪼개지 않는다."""
    name = item.get("ITEM_NAME")
    ingr_raw = item.get("ITEM_INGR_NAME")
    if not name or not ingr_raw:
        return []

    parts = [p.strip() for p in ingr_raw.split("/") if p.strip()]
    expected_cnt = item.get("ITEM_INGR_CNT")
    if expected_cnt and expected_cnt.isdigit() and len(parts) != int(expected_cnt):
        parts = [ingr_raw.strip()]  # 쪼갠 개수가 안 맞으면 원본 통째로 — 조각내지 않음

    return [
        {
            "medicinalproduct": name,
            "ingredient_norm": part,
            "unii": "",
            "method": "사전",
            "confidence": "",
        }
        for part in parts
    ]


def write_csv(pairs: list[dict]) -> None:
    OUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["medicinalproduct", "ingredient_norm", "unii", "method", "confidence"])
        writer.writeheader()
        writer.writerows(pairs)


def main():
    all_pairs = []
    page_no = 1
    total_pages = None
    aborted = False
    while total_pages is None or page_no <= total_pages:
        try:
            items, total_count = fetch_page(page_no)
        except RuntimeError as e:
            print(f"\n중단됨: {e} (그때까지 모은 {len(all_pairs)}개는 저장함)", flush=True)
            aborted = True
            break
        if total_pages is None:
            total_pages = (total_count + PAGE_SIZE - 1) // PAGE_SIZE
            print(f"전체 {total_count}건, {total_pages}페이지", flush=True)
        if not items:
            break
        for item in items:
            all_pairs.extend(extract_pairs(item))
        print(f"page={page_no:>3}/{total_pages}  누적 쌍={len(all_pairs)}", flush=True)
        if page_no % CHECKPOINT_EVERY == 0:
            write_csv(all_pairs)
        page_no += 1
        time.sleep(0.3)

    write_csv(all_pairs)
    unique_products = len({p["medicinalproduct"] for p in all_pairs})
    status = "부분 저장(재시도 소진으로 중단)" if aborted else "수집 완료"
    print(f"\n{status}: 쌍 {len(all_pairs)}개, 고유 제품명 {unique_products}개")
    print("→ output/mfds.csv")


if __name__ == "__main__":
    main()
