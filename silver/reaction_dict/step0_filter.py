"""
0단계: fda_signals.csv에서 부작용이 아닌 signal 제외
제외 기준: 기기 관련, 투약 오류, 라벨링/행정, 효능 문제, 약물상호작용(결과 미명시)
"""
import csv
from pathlib import Path

HERE = Path(__file__).parent

EXCLUDE = {
    "",
    "Accidental exposure to product by child",
    "Adverse event",
    "Device difficult to use",
    "Device malfunction",
    "Device occlusion",
    "Device use confusion (mismatch between the pen device and Instructions for Use)",
    "Drug-device interaction",
    "Drug ineffective",
    "Drug interaction",
    "Drug-drug interaction",
    "False positive radioisotope investigation test result potentially secondary to a drug interaction with venlafaxine",
    "Inappropriate schedule of product administration",
    "Lack of efficacy/effect",
    "Look alike container labels or carton labeling that may contribute to wrong drug errors",
    "Look alike container labels that contribute to wrong drug errors",
    "Look alike container labels that may contribute to wrong drug errors",
    "Look alike containers that may contribute to wrong drug errors",
    "Overdose",
    "Pregnancy, puerperium and perinatal conditions",
    "Product contamination microbial",
    "Product label confusion",
    "Product label confusion contributing to medication error",
    "Product physical issue that may contribute to clogged feeding tubes",
    "Product storage error",
    "Wrong dose errors related to pen and cartridge mismatch",
    "Wrong drug administered",
    "Wrong drug errors related to look alike labeling",
    "Wrong drug errors related to product dosage form identification",
    "Wrong route of administration error (inadvertent intrathecal administration instead of recommended intravenous administration)",
}

def main():
    with open(HERE / "fda_signals.csv", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # signal 정규화: strip + HTML 공백 제거
    for r in rows:
        r["signal"] = r["signal"].replace("\xa0", "").strip()

    kept_signals = sorted({r["signal"] for r in rows if r["signal"] not in EXCLUDE})

    out_path = HERE / "output" / "step0_kept.csv"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["signal"])
        for s in kept_signals:
            writer.writerow([s])

    print(f"전체 고유 signal: {len({r['signal'] for r in rows})}개")
    print(f"제외: {len(EXCLUDE)}개 기준")
    print(f"남은 것: {len(kept_signals)}개  →  output/step0_kept.csv")

if __name__ == "__main__":
    main()
