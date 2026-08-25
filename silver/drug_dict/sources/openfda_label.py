"""
C세부1 — openFDA 라벨 API에서 브랜드명↔성분명 쌍 추출
bulk download 방식 — 전체 262,032건, 14개 zip 파일 (skip 상한 문제 없음)
"""
import csv
from pathlib import Path

from bulk_download import get_partition_urls, fetch_records

HERE = Path(__file__).parent
OUT_PATH = HERE / "output" / "openfda_label.csv"


def extract_pairs(record: dict) -> list[dict]:
    """한 라벨 레코드에서 (브랜드, 성분, UNII) 쌍 추출 — drug_ingredient_map 스키마"""
    meta = record.get("openfda", {})
    brands = meta.get("brand_name", [])
    substances = meta.get("substance_name", [])
    uniis = meta.get("unii", [])
    if not brands or not substances:
        return []
    pairs = []
    for brand in brands:
        for i, substance in enumerate(substances):
            pairs.append({
                "medicinalproduct": brand,
                "ingredient_norm": substance,
                "unii": uniis[i] if i < len(uniis) else "",
                "method": "사전",
                "confidence": "",
            })
    return pairs


def write_csv(pairs: list[dict]) -> None:
    OUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["medicinalproduct", "ingredient_norm", "unii", "method", "confidence"])
        writer.writeheader()
        writer.writerows(pairs)


def main():
    urls, total_records = get_partition_urls("drug", "label")
    print(f"전체 {total_records}건, {len(urls)}개 파일", flush=True)

    all_pairs = []
    aborted = False
    for i, url in enumerate(urls, 1):
        try:
            records = fetch_records(url)
        except RuntimeError as e:
            print(f"\n중단됨: {e} (그때까지 모은 {len(all_pairs)}개는 저장함)", flush=True)
            aborted = True
            break
        for record in records:
            all_pairs.extend(extract_pairs(record))
        print(f"{i}/{len(urls)}  {url.rsplit('/', 1)[-1]}  누적 쌍={len(all_pairs)}", flush=True)
        write_csv(all_pairs)

    unique_brands = len({p["medicinalproduct"] for p in all_pairs})
    status = "부분 저장(재시도 소진으로 중단)" if aborted else "수집 완료"
    print(f"\n{status}: 쌍 {len(all_pairs)}개, 고유 브랜드명 {unique_brands}개")
    print("→ output/openfda_label.csv")


if __name__ == "__main__":
    main()
