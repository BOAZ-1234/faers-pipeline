"""정답지 라벨 감사 워크시트 생성 — 자동 3분류(양성/음성/보류) 사람 대조용.

배경(계획서 12장, action-plan 5장):
  라벨은 장수연 0단계 `classify_signals` 가 **키워드 규칙**으로 자동으로 붙였다.
  한 신호보고서(info) 안에 여러 제품의 상반된 결과가 섞이면
  (예: "Entyvio was updated ... for Tysabri no action was necessary")
  코드가 제품명의 문장 내 위치로 기계 귀속하므로 **오분류 위험**이 있다.
  이 모듈은 그 위험군을 census 로 뽑아 사람이 대조할 워크시트를 만든다.

"200건" 근거(★): 임의 숫자가 아니라 **혼합건 전수**(대조필수) + 비혼합 무작위표본
  (기저율 점검)으로 구성한다. 혼합건 수는 실데이터에서 실측된다(현재 2,268건 중 335건).

주의 — 그룹 기준은 source_url 이 아니라 info 텍스트 블록이다.
  source_url 은 분기 보고서 페이지라 한 분기 100+쌍이 공유한다(그룹 무의미).
  진짜 "한 보고서에 여러 제품 섞임"은 **같은 info 블록**을 여러 (product,signal)
  이 공유하는 경우다(예: 성분 5종 나열 후 일부만 조치). 이때 자동 귀속이 갈린다.

산출: labelset_audit.csv — 사람이 human_label / match / note 칸을 채워 되돌려준다.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import pandas as pd

# 혼합 판정 키워드 — 한 info 에 '조치'와 '불요'가 함께 있으면 제품별 귀속이 필요하다.
_UPDATE_HINT = r"updated|revised|boxed|withdr|safety communication|warning letter"
_NOACTION_HINT = r"no action|not associated|no regulatory|no further regulatory"

# 워크시트 컬럼 순서 (사람이 채우는 칸은 human_label / match / note)
_SHEET_COLS = [
    "audit_id",
    "priority",         # 대조필수(혼합) / 표본(비혼합 무작위)
    "block_id",         # 같은 info 텍스트 블록 → 같은 id (다제품 대조용 정렬 키)
    "n_pairs_in_block",  # 이 info 블록을 공유하는 (product,signal) 쌍 수 (>1 = 다제품)
    "product",
    "signal",
    "auto_label",       # 자동 분류 결과 (label3)
    "human_label",      # ← 사람이 채움: 양성 / 음성 / 보류
    "match",            # ← 사람이 채움 or 스크립트 대조: Y/N
    "note",             # ← 사람이 채움: 근거·이견
    "source_url",       # 원본 보고서 (분기 페이지)
    "info",             # 원문 (판정 근거)
]


def _block_id(info: pd.Series) -> pd.Series:
    """info 텍스트 → 안정적 8자리 블록 id (같은 원문 = 같은 id)."""
    return info.fillna("").map(
        lambda t: hashlib.sha1(t.encode("utf-8")).hexdigest()[:8]
    )


def _flag_mixed(info: pd.Series) -> pd.Series:
    """info 에 '조치'와 '불요' 힌트가 동시에 있으면 True (제품별 귀속 필요)."""
    t = info.fillna("").str.lower()
    return t.str.contains(_UPDATE_HINT, regex=True) & t.str.contains(
        _NOACTION_HINT, regex=True
    )


def build_audit_sheet(
    df: pd.DataFrame,
    *,
    sample_n: int = 100,
    seed: int = 20260915,
) -> pd.DataFrame:
    """라벨된 정답 표 → 감사 워크시트.

    - 대조필수 = 혼합건 전수 (자동 귀속이 틀릴 수 있는 곳, census)
    - 표본     = 비혼합건에서 무작위 sample_n 건 (기저율/명백건 오류 점검)
    같은 report(source_url) 의 여러 제품이 인접하도록 정렬한다.
    """
    d = df.copy()
    if "label3" in d.columns and "auto_label" not in d.columns:
        d = d.rename(columns={"label3": "auto_label"})
    if "source_url" not in d.columns:
        d["source_url"] = ""

    # info 텍스트 블록 단위로 묶는다 (같은 원문을 공유하는 쌍 = 섞임 위험 지점)
    d["block_id"] = _block_id(d["info"])
    d["n_pairs_in_block"] = d.groupby("block_id")["block_id"].transform("size")

    mixed = _flag_mixed(d["info"])
    required = d[mixed].copy()
    required["priority"] = "대조필수"

    pool = d[~mixed]
    take = min(sample_n, len(pool))
    sample = pool.sample(n=take, random_state=seed).copy() if take else pool.iloc[0:0].copy()
    sample["priority"] = "표본"

    sheet = pd.concat([required, sample], ignore_index=True)

    # 사람 기입 칸
    sheet["human_label"] = ""
    sheet["match"] = ""
    sheet["note"] = ""

    # 대조필수 먼저, 그 안에서 같은 info 블록의 여러 제품이 인접하도록
    sheet["_prio_rank"] = (sheet["priority"] == "표본").astype(int)
    sheet = sheet.sort_values(
        ["_prio_rank", "block_id", "product"], kind="stable"
    ).reset_index(drop=True)
    sheet["audit_id"] = range(1, len(sheet) + 1)

    return sheet[_SHEET_COLS]


def _summary(df: pd.DataFrame, sheet: pd.DataFrame) -> str:
    mixed_n = int(_flag_mixed(df["info"]).sum())
    req_n = int((sheet["priority"] == "대조필수").sum())
    smp_n = int((sheet["priority"] == "표본").sum())
    multi = int((sheet["n_pairs_in_block"] > 1).sum())
    lines = [
        "=" * 60,
        "  라벨 분류 감사 워크시트",
        "=" * 60,
        f"  정답지 전체              : {len(df):,} 쌍",
        f"  자동 분포                : "
        + " / ".join(
            f"{k} {int(v)}" for k, v in df.get(
                "auto_label", df.get("label3", pd.Series(dtype=str))
            ).value_counts().items()
        ),
        f"  혼합건(대조필수, census) : {mixed_n:,} 건  ← '200' 대체 근거",
        f"  워크시트 총              : {len(sheet):,} 행 (대조필수 {req_n} + 표본 {smp_n})",
        f"    · 다제품 블록 공유 행   : {multi:,} (n_pairs_in_block > 1)",
        "=" * 60,
    ]
    return "\n".join(lines)


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    default_in = repo / "_probe" / "out" / "fda_signals_labeled.csv"
    default_out = repo / "_probe" / "out" / "labelset_audit.csv"

    ap = argparse.ArgumentParser(description="정답지 라벨 감사 워크시트 생성")
    ap.add_argument("--in", dest="inp", type=Path, default=default_in,
                    help="라벨된 정답 CSV (label3 컬럼 포함)")
    ap.add_argument("--out", dest="out", type=Path, default=default_out,
                    help="감사 워크시트 출력 CSV")
    ap.add_argument("--sample", type=int, default=100,
                    help="비혼합건에서 뽑을 기저율 점검 표본 수")
    ap.add_argument("--seed", type=int, default=20260915, help="표본 추출 시드")
    args = ap.parse_args()

    df = pd.read_csv(args.inp)
    sheet = build_audit_sheet(df, sample_n=args.sample, seed=args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sheet.to_csv(args.out, index=False, encoding="utf-8-sig")

    print(_summary(df, sheet))
    print(f"\n  → {args.out}")


if __name__ == "__main__":
    main()
