"""
C세부1 — RxNorm에서 브랜드명↔성분명 쌍 추출
전체 브랜드(BN) 5,123개 — bulk 다운로드 대안이 없어 브랜드당 API 호출 1회(~0.85s), 약 90분

동의어·철자변형(approximateTerm.json)은 여기서 안 다룬다 — 미리 뽑아둘 수 있는
고정 목록이 아니라 쿼리 시점 실시간 매칭 API라, C세부2(2단계 유사도 매칭) 영역이다.
"""
import csv
import json
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
BASE_URL = "https://rxnav.nlm.nih.gov/REST"
MAX_BRANDS = 5123  # 전체
OUT_PATH = HERE / "output" / "rxnorm.csv"
CHECKPOINT_EVERY = 20


def fetch(url: str, retries: int = 3) -> dict:
    """실패하면 재시도. retries 다 소진하면 예외를 던진다."""
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except Exception as e:
            wait = 2 ** attempt
            print(f"  [{url}] 요청 실패({e}) — {wait}s 후 재시도 ({attempt + 1}/{retries})", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"{url} 요청이 {retries}번 모두 실패")


def fetch_brands() -> list[dict]:
    """브랜드명(BN) 전체 목록 — 한 번의 호출로 다 옴 (페이지네이션 없음)"""
    data = fetch(f"{BASE_URL}/allconcepts.json?tty=BN")
    return data.get("minConceptGroup", {}).get("minConcept", [])


def fetch_ingredients(rxcui: str) -> list[dict]:
    """브랜드 rxcui → 성분(IN 우선, 없으면 PIN) concept 목록"""
    data = fetch(f"{BASE_URL}/rxcui/{rxcui}/related.json?tty=IN+PIN+MIN")
    groups = {g["tty"]: g.get("conceptProperties", []) or [] for g in data["relatedGroup"]["conceptGroup"]}
    return groups.get("IN") or groups.get("PIN") or groups.get("MIN") or []


def write_csv(pairs: list[dict]) -> None:
    OUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["medicinalproduct", "ingredient_norm", "unii", "method", "confidence"])
        writer.writeheader()
        writer.writerows(pairs)


def main():
    brands = fetch_brands()
    unique_brands_total = len(brands)
    if MAX_BRANDS < unique_brands_total:
        print(f"브랜드 전체 {unique_brands_total}개 중 처음 {MAX_BRANDS}개로 v0 진행", flush=True)
    else:
        print(f"브랜드 전체 {unique_brands_total}개 처리", flush=True)
    brands = brands[:MAX_BRANDS]

    all_pairs = []
    aborted = False
    for i, brand in enumerate(brands, 1):
        try:
            ingredients = fetch_ingredients(brand["rxcui"])
        except RuntimeError as e:
            print(f"\n중단됨: {e} (그때까지 모은 {len(all_pairs)}개는 저장함)", flush=True)
            aborted = True
            break
        for ing in ingredients:
            all_pairs.append({
                "medicinalproduct": brand["name"],
                "ingredient_norm": ing["name"],
                "unii": "",  # RxNorm 자체 concept properties엔 UNII가 없음 (openFDA 쪽에서 채워짐)
                "method": "사전",
                "confidence": "",
            })
        if i % 10 == 0:
            print(f"{i}/{len(brands)}  누적 쌍={len(all_pairs)}", flush=True)
        if i % CHECKPOINT_EVERY == 0:
            write_csv(all_pairs)
        time.sleep(0.3)

    write_csv(all_pairs)
    unique_brands = len({p["medicinalproduct"] for p in all_pairs})
    status = "부분 저장(재시도 소진으로 중단)" if aborted else "수집 완료"
    print(f"\n{status}: 브랜드 {i}/{len(brands)}개 처리, 쌍 {len(all_pairs)}개, 성분 매칭된 고유 브랜드 {unique_brands}개")
    print("→ output/rxnorm.csv")
    if not aborted and MAX_BRANDS < unique_brands_total:
        print(f"⚠ 전체 브랜드 {unique_brands_total}개 중 처음 {MAX_BRANDS}개만 (v0). 전체 커버리지는 이후 확장")


if __name__ == "__main__":
    main()
