"""v2 시점(PIT) 백테스트 — 리드타임 평가 (스캐폴드).

v2 의 핵심은 "FDA 조치 **이전** 데이터만으로 그 신호가 떴나, 얼마나 앞서 떴나"다
(= 리드타임, 컨퍼런스 와우포인트). 전 기간 합산본(parquet)으론 못 하고, 시점별
스냅샷이 필요하다:

  build_signal_scores.py --as-of <YYYYMMDD>   # fda_dt < as_of 만 집계
  → 여러 as_of 로 돌려 시점별 신호표를 만든 뒤 이 모듈로 평가.

반응/성분 키 맞추기는 labelmap 이 끝냈다고 보고(=positives 는 이미 SignalKey),
여기선 "각 양성이 언제 처음 게이트를 통과(신호로 떴)했나" 만 본다.

상태: 스캐폴드 — 평가 로직·테스트는 완성, 실데이터 스냅샷은 EC2 as_of 집계가 와야
채워진다(아직 Drugs@FDA 조치일/as_of 스냅샷 미생성). 합성 데이터로 검증됨.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import pandas as pd

from scoring.keys import SignalKey, make_key
from scoring.ground_truth import QMAP
from scoring.ranking import Gate


def quarter_index(year: int, q_start: str) -> int:
    """**선형** 분기 인덱스 = year*4 + (분기-1). 2015 Q1→8060, Q3→8062.

    선형이라 뺄셈이 곧 '분기 수 차이'(리드타임 단위)가 된다. 연 경계도 안전
    (2015Q4=8063, 2016Q1=8064, 차=1분기). 정렬도 유지.
    """
    return int(year) * 4 + (QMAP.get(str(q_start), 1) - 1)


def detected_keys(
    signals: pd.DataFrame,
    *,
    gate: Gate | None = None,
    ingredient_col: str = "ingredient",
    pt_col: str = "pt",
) -> set[SignalKey]:
    """시점 스냅샷(신호표) → 그 시점에 '신호로 뜬'(게이트 통과) 키 집합.

    gate None 이면 IC025>0 를 기본 탐지 기준으로 본다(a 무관).
    """
    df = signals
    if gate is not None:
        df = df[df["a"] >= gate.a_min]
        if gate.ic025_min is not None:
            df = df[df["ic025"] > gate.ic025_min]
    else:
        df = df[df["ic025"] > 0]
    return {make_key(i, p) for i, p in zip(df[ingredient_col], df[pt_col])}


@dataclass(frozen=True)
class LeadRow:
    key: SignalKey
    action_asof: int          # FDA 조치 시점 (분기/날짜 정수)
    first_detected: int | None  # 최초 탐지 as_of (없으면 None)
    detected_before: bool     # 조치 이전에 떴나
    lead: int | None          # action - first_detected (양수면 앞서 떴다)


def leadtime(
    positives_asof: dict[SignalKey, int],
    snapshots: dict[int, set[SignalKey]],
) -> list[LeadRow]:
    """각 양성의 최초 탐지 시점·리드 계산.

    positives_asof : {양성 키 → 그 신호의 FDA 조치 as_of(정수)}
    snapshots      : {as_of(정수) → 그 시점에 탐지된 키 집합(detected_keys 결과)}
    """
    asofs = sorted(snapshots)
    rows: list[LeadRow] = []
    for key, action in positives_asof.items():
        first = next((a for a in asofs if key in snapshots[a]), None)
        before = first is not None and first < action
        lead = (action - first) if first is not None else None
        rows.append(LeadRow(key, action, first, before, lead))
    return rows


def leadtime_from_first(
    positives_asof: dict[SignalKey, int],
    first_detected: dict[SignalKey, int],
) -> list[LeadRow]:
    """스냅샷 대신 '최초 탐지 분기'(build_first_detection.py 결과)로 바로 계산.

    positives_asof : {양성 키 → FDA 조치 분기(선형)}
    first_detected : {키 → 최초 게이트 통과 분기(선형)}
    """
    rows: list[LeadRow] = []
    for key, action in positives_asof.items():
        first = first_detected.get(key)
        before = first is not None and first < action
        lead = (action - first) if first is not None else None
        rows.append(LeadRow(key, action, first, before, lead))
    return rows


def summarize(rows: list[LeadRow]) -> dict:
    """리드타임 요약 — 탐지율·중앙값 리드."""
    n = len(rows)
    det_before = [r for r in rows if r.detected_before]
    leads = sorted(r.lead for r in det_before if r.lead is not None)
    ever = sum(1 for r in rows if r.first_detected is not None)
    return {
        "n_positives": n,
        "detected_ever": ever,
        "detected_before_action": len(det_before),
        "detection_rate_before": len(det_before) / n if n else 0.0,
        "median_lead": statistics.median(leads) if leads else None,
        "max_lead": max(leads) if leads else None,
    }
