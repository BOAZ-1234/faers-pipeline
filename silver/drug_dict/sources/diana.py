"""
C세부1 — DiAna dictionary(Fusaroli et al., Drug Saf 2024, PMC10874306, MIT 라이선스)에서
FAERS 원본 drugname↔성분(Substance) 쌍 추출

다른 4개 소스와 근본적으로 다르다 — openFDA·RxNorm·식약처는 "공식 제품명" 사전이라
FAERS 자유기재 텍스트와 정확히 안 겹치는 경우가 많은데, DiAna는 FAERS에 실제로
등장한 원본 문자열(예: "olmetec (olmesartan medoxomil) (tablet)")을 키로 직접 매핑해뒀다.
그래서 build_map.py에서 이 소스만 UNII 없이 들어가고, match.py 조회 시에도
normalize_query()로 다듬지 않은 원본 문자열 그대로 대조해야 효과가 난다(원본이 이미
정리된 형태라 재정규화하면 오히려 어긋남).

원본 858,572행 중 "NA"(DiAna가 아직 못 정한 것)는 우리도 못 쓰니 제외한다.
"""
import csv
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
DIANA_URL = "https://osf.io/download/n2dgz/"
OUT_PATH = HERE / "output" / "diana.csv"


def fetch_csv() -> str:
    with urllib.request.urlopen(DIANA_URL, timeout=120) as r:
        return r.read().decode("utf-8-sig")


def extract_pairs(raw_csv_text: str) -> list[dict]:
    pairs = []
    reader = csv.DictReader(raw_csv_text.splitlines(), delimiter=";")
    for row in reader:
        name = row.get("drugname", "").strip()
        substance = row.get("Substance", "").strip()
        if not name or not substance or substance == "NA":
            continue
        for ingr in substance.split(";"):
            ingr = ingr.strip()
            if ingr:
                pairs.append({
                    "medicinalproduct": name,
                    "ingredient_norm": ingr,
                    "unii": "",
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
    print("DiAna_dictionary.csv 다운로드 중...", flush=True)
    raw_text = fetch_csv()
    pairs = extract_pairs(raw_text)
    write_csv(pairs)
    unique_products = len({p["medicinalproduct"] for p in pairs})
    print(f"추출 완료: 쌍 {len(pairs)}개, 고유 drugname {unique_products}개")
    print("→ output/diana.csv")


if __name__ == "__main__":
    main()
