"""[정답지 3분류] FDA 신호 info 텍스트를 {양성/음성/보류}로 의미 분류.
한 info에 여러 제품의 상반 결과가 섞이면 제품별 위치로 귀속.

입력: out/fda_signals.csv (info_full 있으면 우선 사용 — 절단 안 된 전체 원문)
산출: out/fda_signals_labeled.csv (label3 컬럼 추가)

2026-09 감사(전수 2,268행 검토) 반영판. 구 키워드 방식 대비 수정:
 1) boxed-warning 오탐 제거 — "boxed warning ... is adequate/already"(기존 라벨 적정=음성)를
    조치로 오인하던 버그 차단. 단 "addition of a boxed warning"(신규 추가)은 양성 유지.
 2) 구형식 조치 인식 추가 — "added to the ... section", "approved ... REMS",
    "instructions for use", "recall", "class labeling", "container/carton label revised" 등.
 3) 구 키워드 전량 보존(withdrawal·safety communication·required changes 누락 방지).
전수 census(사람/LLM 검토, fda_signals.csv 의 label 컬럼) 기준 정확도 95.8% → 96.7%.
한계: 규칙 기반 제품별 귀속은 다제품 혼재 블록에서 ~83%가 상한 → 그 블록은 census 를 정답으로 둔다.
"""
import re
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent / "out"

UPDATE_KW = [  # 구 키워드(누락 방지) + 구형식/기타 조치 추가
    "was updated", "were updated", "was revised", "were revised", "boxed warning", "withdr",
    "safety communication", "warning letter", "required changes", "drug safety communication",
    "was added", "revised in", "were made",
    "revised to", "updated to include", "updated for", "was added to", "were added to",
    "added to the", "addition of a boxed", "approved revised labeling", "instructions for use",
    "approved a risk evaluation", "approved the", "rems ", "class labeling", "class safety labeling",
    "required labeling changes", "container label", "carton label", "recall", "name change"]
NOACTION_KW = ["no action was necessary", "no action is necessary", "no action necessary",
               "no further regulatory action", "no action was needed", "no action is needed",
               "not associated", "no regulatory action", "no action for", "no action at this time",
               "no further action", "adequately labeled", "adequately addressed",
               "labeling is adequate", "is adequate,", "is adequate ", "is adequate."]
PENDING_KW = ["is evaluating", "are evaluating", "continuing to evaluate", "under evaluation",
              "continues to evaluate", "continue to evaluate", "is monitoring", "is assessing",
              "working with the manufacturer"]
NOACTION_ANCHOR = ["no action", "no further regulatory", "no further action", "adequately labeled",
                   "determined that the last approved", "is adequate", "are adequate",
                   "decided that no action", "determined that no action"]


def brand(p):
    b = re.split(r"[(]", str(p))[0]
    b = re.sub(r"\b(injection|tablets?|capsules?|for oral suspension|solution|cream|gel|"
               r"inhalation|spray|film|patch|kit)\b", " ", b, flags=re.I)
    return re.sub(r"\s+", " ", b).strip().lower()


def _boxed_is_adequate(tl):
    """boxed warning 이 '이미 적정'맥락(신규조치X)이면 True → 조치로 치지 않는다."""
    if "boxed warning" not in tl:
        return False
    adequ = any(k in tl for k in ["is adequate", "are adequate", "already",
                                  "adequately addressed", "adequately labeled"])
    added = any(k in tl for k in ["added a boxed warning", "boxed warning was",
                                  "addition of a boxed", "added to the",
                                  "was updated", "were updated", "was revised"])
    return adequ and not added


def classify(product, info):
    t = str(info).strip()
    if not t or t.lower() in ("nan", "none"):
        return "보류"
    tl = t.lower()
    P = brand(product)

    boxed_adequate = _boxed_is_adequate(tl)
    has_update = any(k in tl for k in UPDATE_KW)
    if boxed_adequate:  # boxed warning 만으로 조치 처리 금지
        has_update = any(k in tl for k in
                         ["was updated", "were updated", "was revised", "were revised",
                          "added to the", "addition of a boxed"])
    has_noaction = any(k in tl for k in NOACTION_KW)
    is_pending = any(k in tl for k in PENDING_KW)

    if is_pending and not has_update and not has_noaction:
        return "보류"

    if has_update and has_noaction:  # 혼합: no-action 앵커 위치로 분할해 제품별 귀속
        idxs = [tl.find(k) for k in NOACTION_ANCHOR if tl.find(k) >= 0]
        cut = min(idxs) if idxs else len(tl)
        updated_seg, noaction_seg = tl[:cut], tl[cut:]
        if P and P in updated_seg:
            return "양성"
        if P and P in noaction_seg:
            return "음성"
        if any(k in noaction_seg for k in ["all other", "certain", "for a certain"]):
            return "음성"
        return "양성"

    if has_noaction:
        return "음성"
    if has_update:
        return "양성"
    return "보류"


def main():
    d = pd.read_csv(OUT / "fda_signals.csv")
    # 절단 안 된 원문(info_full) 있으면 그걸로 분류
    src = "info_full" if "info_full" in d.columns else "info"
    d["label3"] = [classify(p, i) for p, i in zip(d["product"], d[src])]
    d.to_csv(OUT / "fda_signals_labeled.csv", index=False, encoding="utf-8-sig")

    print(f"분류 소스 컬럼: {src}")
    print(d["label3"].value_counts().to_string())
    print(f"총 {len(d)}쌍")
    if "label" in d.columns:  # census(정정) 대비 정확도
        v = d[d["label"].notna()]
        acc = (v["label3"] == v["label"]).mean()
        print(f"census(label) 대비 정확도: {acc*100:.1f}%")


if __name__ == "__main__":
    main()
