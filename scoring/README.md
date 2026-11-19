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
| `ranking.py` | **순위표 생성 — 버전 사다리 v1~v5** (게이트·라벨필터·shrinkage logreg 결합) |
| `mappers.py` | gold product/signal → 순위표 키 매퍼(사전 주입, 없으면 항등+경고) |
| `backtest.py` | **백테스트 드라이버 CLI** — fct_signal_scores 를 정답지로 채점 |

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

## 버전 사다리 (v1~v5) — `ranking.py` + `backtest.py`

계획서 §4-7 "한꺼번에 만들지 않는다" 를 코드로. 순위표를 `fct_signal_scores`(GOLD)에서
만들고, 지표를 하나씩 얹는다.

| 버전 | 추가 | 결합 | 데이터 상태 |
|---|---|---|---|
| **v1** | IC025 단독 | ic025 정렬 | ✅ 지금 실행 가능 (`ic025`·`a`만 필요) |
| v2 | + 시점 라벨 필터(이진 제거) | ic025 정렬 | ⏳ Drugs@FDA 시점 라벨 집합 필요 (`label_excludes`) |
| v3 | + 중대성(사망분율) | shrinkage logreg | ⏳ signals 에 `death_frac` 컬럼 필요 |
| v4 | + 끊음/재투여 | shrinkage logreg | ⏳ `dechal_frac`·`rechal_frac` 필요 |
| v5 | + 최근성 | shrinkage logreg | ⏳ `recency` 필요 |

- **게이트**(a≥3, IC025>0)·**라벨 필터**는 **고정**(WHO-UMC/MHRA 표준). **랭킹 가중치만 학습**
  (shrinkage logistic regression, numpy 구현 — sklearn 의존 없음). 계획서 §4-7.
- v3+ 피처 컬럼이 없으면 `FeatureUnavailable` 로 "무슨 컬럼이 어디서 와야 하는지" 알려준다.
  가짜로 채우지 않는다. 피처 테이블이 생기면 `(ingredient, pt)` 로 조인해 넘기면 바로 돈다.

```bash
# v1 — 지금 바로 (signals 는 EC2 에서 fct_signal_scores export, 사전은 EC2 산출물)
python -m scoring.backtest --signals fct_signal_scores.csv \
       --version v1 --gate \
       --drug-map silver/drug_dict/drug_ingredient_map.csv \
       --reaction-dict silver/reaction_dict/reaction_dict_final.csv

# 여러 버전 한 번에
python -m scoring.backtest --signals signals_with_features.parquet \
       --version v1 --version v2 --version v3 --out out.json
```

> 먼저 성적표의 **coverage**(양성 중 채점 가능 비율 = Recall 상한)를 확인할 것. 0 에 가까우면
> 매퍼(사전) 주입이 안 된 것 — 지표를 보기 전에 조인키부터 맞춰야 한다(노트 ③).

## 핵심 설계 — 왜 정규화를 안 하나

채점기는 **정규화를 하지 않는다.** 성분/부작용 정규화는 상류 사전(이수연 `drug_dict`, 하경 `reaction_dict`)의 책임이고, 채점기는 그 결과에 `canonical()` 하나만 더 걸어 완전일치를 본다. 이렇게 해야 순위표와 정답지가 **같은 키**를 쓰게 되고(계획서 노트 ③), 나중에 사전을 붙일 때 매퍼만 주입하면 되어 재작업이 없다.
