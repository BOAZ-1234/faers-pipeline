"""labelset — FDA 분기 신호보고서 정답지 수집·라벨링.

정답지(채점 기준) 파이프라인. 계약: contracts/labelset.md
  download_fda_signals  → out/fda_signals.csv (product·signal·info·info_full·source_url)
  classify_signals      → label3 ∈ {양성, 음성, 보류} (info_full 기준)
  (감사 정정본 label 은 fda_signals.csv 의 label 컬럼)
하류: scoring.ground_truth.build_labelset 가 이 표를 코호트 분할해 채점셋으로.
"""
from labelset.classify_signals import classify

__all__ = ["classify"]
