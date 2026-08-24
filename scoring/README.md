# scoring — 채점기

우리 순위표(FAERS 후보 신호)를 FDA 정답지로 채점한다. **랜덤 순위표로 먼저 전체가 돌아가는** 독립 모듈.

## 구성

| 파일 | 역할 |
|---|---|
| `keys.py` | 조인 키 `SignalKey`, `canonical()` — 순위표·정답지 공통 정규화 (노트 ③의 "같은 키") |
| `metrics.py` | Recall@K / Precision@K / Lift / 검토대상 축소율 — 순수함수 |
| `scorer.py` | `score(ranking, positives)` → `Scorecard` (완전일치 매칭) |
| `ground_truth.py` | 정답 표 → 코호트 종결·컷오프 분할 → `LabelSet`(채점셋/봉인예측셋) |
| `random_ranking.py` | 가짜 순위표 생성기 (스모크용) |
| `smoke.py` | 실데이터 없이 전 구간 검증 |

계약 문서: [`contracts/ranking.md`](../contracts/ranking.md), [`contracts/labelset.md`](../contracts/labelset.md)

## 빠른 시작

```bash
python -m scoring.smoke     # 전체 스모크 (랜덤 vs 신호 심은 순위표)
pytest                      # 단위 + 통합 테스트
```

```python
from scoring import make_key, score
from scoring.ground_truth import build_labelset

# 1) 정답지 → 라벨셋 (이수연/하경 사전을 매퍼로 주입해 키 일치)
ls = build_labelset(labeled_df,
                    ingredient_of=drug_map.get,     # product → ingredient_norm
                    reaction_of=reaction_dict.get)  # signal  → reaction_pt

# 2) 우리 순위표 (신호 강한 순)
ranking = [make_key(i, r) for i, r in ranked_pairs_desc]

# 3) 채점
card = score(ranking, ls.positives, universe_size=N)
print(card.render())
```

## 핵심 설계 — 왜 정규화를 안 하나

채점기는 **정규화를 하지 않는다.** 성분/부작용 정규화는 상류 사전(이수연 `drug_dict`, 하경 `reaction_dict`)의 책임이고, 채점기는 그 결과에 `canonical()` 하나만 더 걸어 완전일치를 본다. 이렇게 해야 순위표와 정답지가 **같은 키**를 쓰게 되고(계획서 노트 ③), 나중에 사전을 붙일 때 매퍼만 주입하면 되어 재작업이 없다.
