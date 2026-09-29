"""labelset — FDA 분기 신호보고서 정답지 수집·라벨링.

정답지(채점 기준) 파이프라인. 계약: contracts/labelset.md
  download_fda_signals  → out/fda_signals.csv (product·signal·info·info_full·source_url, gitignore)
  classify_signals      → label3 ∈ {양성, 음성, 보류} (info_full 기준, 자동분류 참고값)
  감사(사람+LLM)         → labelset/data/labelset_gold.csv 의 label (채점 정답, git 추적)
하류: scoring.ground_truth.load_gold → build_labelset 가 gold 를 코호트 분할해 채점셋으로.
채점은 감사본 label 로만 한다 — label3 는 정확도 점검용이며 gold 에 넣지 않는다.
"""
from labelset.classify_signals import classify

__all__ = ["classify"]
