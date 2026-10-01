"""
C단계 캐스케이드 평가용 DiAna held-out 정답지 세팅 (이슈 #29, §8·§12)

DiAna(Fusaroli 2024, PMC10874306)는 FAERS 원본을 독립 수동 검수한 사전이라,
우리 사전(openFDA·NDC·RxNorm·식약처)과 출처가 분리돼 leakage가 적은 채점 기준이 된다.
그래서 사전(build_map.py)에는 넣지 않고 정답지로만 쓴다 — 이 파일은 그 정답지를 만든다.

정답지 vs 사전의 결정적 차이:
  - raw_drug_name: **정규화하지 않은 원본 그대로** 둔다. held-out 벤치는 캐스케이드가
    "처음 보는 원본 이름"을 넣었을 때 맞히는지 보는 것이라, 여기서 미리 다듬으면
    Stage 0(normalize)을 건너뛴 채로 채점하게 된다 — 캐스케이드가 제 normalize_query()로
    알아서 다듬어야 공정하다.
  - ingredient_norm: 사전과 동일한 normalize_ingredient()(build_map.py)로 정규화한다.
    대문자·공백단일화·염/수화물 접미사 제거(HYDROCHLORIDE 등). 사전이 OXYCODONE으로
    답을 내는데 정답지가 OXYCODONE HYDROCHLORIDE면 맞혀도 오답 처리되기 때문.

수동 검수분 필터(§DiAna 논문: 보고 ≥200건이 수동 검수 대상, 14,832 term, 96.88% 커버):
  DiAna 공개 CSV엔 검수 플래그가 없어서, FAERS drugname 빈도(coverage.py가 S3에서 뽑는 것)로
  ≥200건만 남긴다. 그 빈도 CSV(name,n_reports)를 --freq-csv 로 넘기면 필터를 적용하고,
  없으면 전체를 통과시키되 경고를 찍는다(빈도는 이수연 커버리지 측정에서 나오므로, 나오면
  인자만 넣어 재실행하면 된다).

실행:
  python3 diana_golden.py                         # 다운로드 → 정답지 (빈도필터 없음)
  python3 diana_golden.py --freq-csv counts.csv   # 빈도 필터 적용 (≥200건)
  python3 diana_golden.py --freq-csv counts.csv --min-reports 200 --seed 42
"""
import argparse
import csv
import random
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from build_map import normalize_ingredient          # 염접미사 제거 포함, 사전과 동일
from sources.diana import fetch_csv                # 다운로드 로직 중복 제거

OUT_DIR = HERE / "output"

FIELDS = ["raw_drug_name", "ingredient_norm", "unii", "source"]
MIN_REPORTS_DEFAULT = 200  # DiAna 논문 수동 검수 컷
TEST_FRACTION = 0.2        # dev/test = 80/20


def extract_pairs(raw_csv_text: str) -> list[dict]:
    """DiAna drugname;Substance → 정답지 행. raw_drug_name은 원본 그대로 보존."""
    pairs = []
    reader = csv.DictReader(raw_csv_text.splitlines(), delimiter=";")
    for row in reader:
        name = row.get("drugname", "").strip()
        substance = row.get("Substance", "").strip()
        if not name or not substance or substance == "NA":
            continue
        for ingr in substance.split(";"):
            ingr = normalize_ingredient(ingr)
            if ingr:
                pairs.append({
                    "raw_drug_name": name,   # 원본 그대로 — 정규화 금지
                    "ingredient_norm": ingr,
                    "unii": "",
                    "source": "diana",
                })
    return pairs


def load_freq(freq_csv: Path) -> dict[str, int]:
    """coverage.py가 뽑은 name,n_reports CSV(첫 줄 헤더) → {대문자name: n_reports}.
    coverage.py는 upper(trim(drugname))로 집계하므로 여기서도 같은 키로 맞춘다."""
    freq = {}
    with open(freq_csv, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # 헤더
        for cols in reader:
            if len(cols) >= 2:
                freq[cols[0].strip().upper()] = int(cols[1])
    return freq


def filter_manual_curated(pairs: list[dict], freq: dict[str, int], min_reports: int) -> list[dict]:
    """FAERS 보고 ≥min_reports 인 drugname만 남긴다(수동 검수분 근사)."""
    return [p for p in pairs if freq.get(p["raw_drug_name"].strip().upper(), 0) >= min_reports]


def split_dev_test(pairs: list[dict], seed: int, test_frac: float) -> tuple[list[dict], list[dict]]:
    """고유 raw_drug_name 단위로 분할 — 같은 약 이름이 dev·test에 갈리면 leakage.
    deterministic: 같은 seed → 같은 분할."""
    names = sorted({p["raw_drug_name"] for p in pairs})
    rng = random.Random(seed)
    rng.shuffle(names)
    n_test = int(len(names) * test_frac)
    test_names = set(names[:n_test])
    dev = [p for p in pairs if p["raw_drug_name"] not in test_names]
    test = [p for p in pairs if p["raw_drug_name"] in test_names]
    return dev, test


def write_csv(path: Path, pairs: list[dict]) -> None:
    path.parent.mkdir(exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(pairs)


def main():
    ap = argparse.ArgumentParser(description="DiAna held-out 정답지 세팅")
    ap.add_argument("--freq-csv", type=Path, help="FAERS drugname 빈도 CSV (name,n_reports) — coverage.py 산출물")
    ap.add_argument("--min-reports", type=int, default=MIN_REPORTS_DEFAULT, help="수동 검수분 컷 (기본 200)")
    ap.add_argument("--seed", type=int, default=42, help="dev/test 분할 seed")
    ap.add_argument("--test-frac", type=float, default=TEST_FRACTION, help="test 비율 (기본 0.2)")
    args = ap.parse_args()

    print("DiAna_dictionary.csv 다운로드 중...", flush=True)
    pairs = extract_pairs(fetch_csv())
    print(f"추출: 쌍 {len(pairs):,}개, 고유 drugname {len({p['raw_drug_name'] for p in pairs}):,}개")

    if args.freq_csv:
        freq = load_freq(args.freq_csv)
        pairs = filter_manual_curated(pairs, freq, args.min_reports)
        print(f"빈도 필터(≥{args.min_reports}건): 쌍 {len(pairs):,}개, "
              f"고유 drugname {len({p['raw_drug_name'] for p in pairs}):,}개")
    else:
        print("⚠ --freq-csv 없음 → 수동 검수 필터 미적용, 전체 통과. "
              "이수연 커버리지 CSV 나오면 --freq-csv 로 재실행할 것.")

    dev, test = split_dev_test(pairs, args.seed, args.test_frac)
    write_csv(OUT_DIR / "diana_golden.csv", pairs)
    write_csv(OUT_DIR / "diana_golden_dev.csv", dev)
    write_csv(OUT_DIR / "diana_golden_test.csv", test)
    print(f"\n정답지 → output/diana_golden.csv ({len(pairs):,}쌍)")
    print(f"  dev  → output/diana_golden_dev.csv  ({len(dev):,}쌍)")
    print(f"  test → output/diana_golden_test.csv ({len(test):,}쌍)")


if __name__ == "__main__":
    main()
