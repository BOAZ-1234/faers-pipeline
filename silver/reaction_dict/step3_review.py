"""
3단계 전수 확인 — 터미널 인터랙티브 리뷰
입력:
  - output/step1_normalized.csv  (85개 자동 매칭)
  - output/step3_llm.csv         (33개 LLM 판정)
출력:
  - output/step3_reviewed.csv    (전체 확인 결과)
  - output/reaction_dict_final.csv (최종 사전: signal → pt_set_id)

사용법:
  python3 step3_review.py
  python3 step3_review.py --from <signal>   # 특정 signal부터 재개
"""
import csv
import sys
from pathlib import Path

HERE = Path(__file__).parent
STEP1_PATH = HERE / "output" / "step1_normalized.csv"
LLM_PATH = HERE / "output" / "step3_llm.csv"
REVIEWED_PATH = HERE / "output" / "step3_reviewed.csv"
FINAL_PATH = HERE / "output" / "reaction_dict_final.csv"

FIELDS = ["signal", "pt_set", "method", "status", "reviewer_note"]


def load_reviewed() -> dict[str, dict]:
    if not REVIEWED_PATH.exists():
        return {}
    with open(REVIEWED_PATH) as f:
        return {r["signal"]: r for r in csv.DictReader(f)}


def save_reviewed(rows: list[dict]) -> None:
    with open(REVIEWED_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def load_all_signals() -> list[dict]:
    """step1(자동) + step3_llm 병합, signal 기준 중복 제거"""
    signals: dict[str, dict] = {}

    with open(STEP1_PATH) as f:
        for row in csv.DictReader(f):
            if row["matched_pt"]:
                signals[row["signal"]] = {
                    "signal": row["signal"],
                    "pt_set": row["matched_pt"].lower(),
                    "method": row["method"],
                    "note": f"count={row['count']}",
                }

    if LLM_PATH.exists():
        with open(LLM_PATH) as f:
            for row in csv.DictReader(f):
                signals[row["signal"]] = {
                    "signal": row["signal"],
                    "pt_set": row["pt_set"],
                    "method": row["method"],
                    "note": row["note"],
                }

    return list(signals.values())


def prompt_review(idx: int, total: int, entry: dict, existing) -> dict:
    """한 signal을 보여주고 accept/edit/skip/quit 입력받기"""
    sig = entry["signal"]
    pt_set = entry["pt_set"]
    method = entry["method"]
    note = entry["note"]

    print(f"\n{'='*60}")
    print(f"[{idx}/{total}]  {sig}")
    print(f"  method   : {method}")
    print(f"  pt_set   : {pt_set or '(없음)'}")
    if note:
        print(f"  note     : {note}")
    if existing:
        print(f"  이전검토  : {existing['status']}  {existing['pt_set']}")

    print()
    print("  a) accept — 그대로 확정")
    print("  e) edit   — pt_set 직접 입력")
    print("  n) none   — 매핑 없음으로 확정")
    print("  s) skip   — 나중에")
    print("  q) quit   — 저장 후 종료")
    print()

    while True:
        choice = input("  선택 [a/e/n/s/q]: ").strip().lower()
        if choice in ("a", "e", "n", "s", "q"):
            break

    reviewer_note = ""
    if choice == "a":
        return {"signal": sig, "pt_set": pt_set, "method": method, "status": "ok", "reviewer_note": ""}
    elif choice == "e":
        new_pt = input("  pt_set 입력 (소문자, | 로 묶음): ").strip()
        reviewer_note = input("  메모 (선택): ").strip()
        return {"signal": sig, "pt_set": new_pt, "method": method, "status": "edited", "reviewer_note": reviewer_note}
    elif choice == "n":
        reviewer_note = input("  이유 메모 (선택): ").strip()
        return {"signal": sig, "pt_set": "", "method": method, "status": "none", "reviewer_note": reviewer_note}
    elif choice == "s":
        return {"signal": sig, "pt_set": pt_set, "method": method, "status": "skipped", "reviewer_note": ""}
    else:  # q
        return None


def write_final(reviewed: list[dict]) -> None:
    """status=ok/edited인 것만 최종 사전으로 추출"""
    final = [
        {"signal": r["signal"], "pt_set_id": r["pt_set"]}
        for r in reviewed
        if r["status"] in ("ok", "edited") and r["pt_set"]
    ]
    with open(FINAL_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["signal", "pt_set_id"])
        w.writeheader()
        w.writerows(final)
    return len(final)


def main():
    # --from 옵션 파싱
    start_from = None
    if "--from" in sys.argv:
        idx = sys.argv.index("--from")
        if idx + 1 < len(sys.argv):
            start_from = sys.argv[idx + 1]

    all_signals = load_all_signals()
    reviewed_map = load_reviewed()
    results = list(reviewed_map.values())  # 이미 검토된 것 유지

    total = len(all_signals)
    skipped_before = start_from is not None
    reviewed_count = 0

    for i, entry in enumerate(all_signals, 1):
        sig = entry["signal"]

        # --from 옵션: 해당 signal 나오기 전까지 건너뜀
        if skipped_before:
            if sig == start_from:
                skipped_before = False
            else:
                continue

        # 이미 ok/edited로 확정된 것은 스킵 (s로 스킵한 건 다시 보여줌)
        existing = reviewed_map.get(sig)
        if existing and existing["status"] in ("ok", "edited", "none"):
            print(f"  [확정됨 skip] {sig}")
            continue

        result = prompt_review(i, total, entry, existing)
        if result is None:
            print("\n저장 후 종료합니다.")
            break

        # 이미 있는 항목이면 업데이트
        results = [r for r in results if r["signal"] != sig]
        results.append(result)
        reviewed_count += 1
        save_reviewed(results)

    n_final = write_final(results)
    done = sum(1 for r in results if r["status"] in ("ok", "edited", "none"))

    print(f"\n확정: {done}개 / 최종 사전: {n_final}개 → {FINAL_PATH.relative_to(HERE.parent.parent)}")
    remaining = total - done
    if remaining > 0:
        print(f"남은 것: {remaining}개  →  python3 step3_review.py 로 재개")


if __name__ == "__main__":
    main()
