# 계약: 정답지 라벨셋 (labelset) — 채점 기준

FDA 분기 신호보고서에서 만든 **정답지**. 채점기의 정답 기준(ground truth)이다.

## 원본 → 라벨셋 흐름

```
FDA 분기 신호보고서 스크랩 (download_fda_signals → out/fda_signals.csv, gitignore)
  → 사람+LLM 전수 감사로 label 정정
  → labelset/data/labelset_gold.csv  (감사본 정답지, git 추적 · 재생성 불가)
  → scoring.ground_truth.load_gold → build_labelset: 쌍 종결 + 코호트 컷오프 분할
```

> **`label` ≠ `label3`.** `label` 은 감사본(사람+LLM 정정)으로 **채점의 정답**이다.
> `classify_signals` 가 만드는 `label3` 는 그 감사본 대비 정확도를 재는 **자동분류 참고값**이며
> **채점에 쓰지 않는다.** gold 에는 `label3` 을 넣지 않는다.

## 입력 표 (build_labelset 의 DataFrame = `load_gold()` 가 읽는 gold)

| 컬럼 | 설명 |
|---|---|
| `product` | 제품명 원문 (브랜드·성분 혼재, `\|` 로 조합약 표기 가능) |
| `signal` | 부작용 표기 원문 |
| `year`, `q_start` | 최초/각 등장 분기 (컷오프 근거) |
| `quarter_label` | 분기 라벨 문자열 (등장 분기 수 집계용) |
| `label` | 3분류 `{양성, 음성, 보류}` — **감사본**(사람+LLM 정정). `classify_signals` 의 `label3`(자동분류)이 아님 |
| `label_basis` | 그 label 을 정한 근거 (감사 기록, 리뷰용) |

> gold 는 스크랩 원문(`info_full`·`info`·`source_url` 등)을 뺀 authored 컬럼만 담는다 —
> 그 원문들은 재생성 가능한 데이터라 git 밖(`out/`) 유지.

## 라벨 의미

| 라벨 | 뜻 | 채점에서의 역할 |
|---|---|---|
| 양성 | FDA가 조치(라벨 업데이트 등) → **진짜 신호** | 정답. 단 Recall 분모는 전체 양성이 아니라 **채점 가능한 양성**(아래) |
| 음성 | FDA가 "조치 불요" 판정 | 기저율·오탐 확인 (정답 아님) |
| 보류 | 아직 평가 중(미판정) | **봉인예측셋** — 미래검증용, 채점 제외 |

### Recall 분모 = "채점 가능한 양성" (계획서 3장 ②)

순위표에 **존재할 수조차 없는** 양성(FAERS 신고 0건, 이름 통일 실패 등)은 분모에서 뺀다.
그렇지 않으면 "우리가 잡을 방법이 없는 것"까지 오답으로 집계된다.
`Scorecard` 는 `n_positives_total`(전체) → `n_positives_scorable`(채점가능) →
`coverage`(=scorable/total, 이름매칭 한계) 를 함께 보고해 한계를 숨기지 않는다.

## 분할 규칙 (우측절단 대응)

- 한 쌍이 여러 분기에 등장하면 **종결 라벨**을 채택: `양성 > 음성 > 보류`.
- **최초등장 연도별 판정완료율**(=(양성+음성)/전체)이 기준(기본 0.75) 이상인 가장 최근 연도까지를 컷오프로 삼는다. (예: 2023은 포함, 판정완료율 절벽인 2024~는 제외)
- **채점셋** = 판정완료(양성/음성) & 최초등장 ≤ 컷오프.
- **봉인예측셋** = 보류. 3~6개월 뒤 실제 조치 여부로 사후 검증.

## 키 규칙 (노트 ③)

- `build_labelset(df, ingredient_of=..., reaction_of=...)` 에 매퍼를 주입하면
  `product → ingredient_norm`, `signal → reaction_pt` 로 바뀌어 **순위표와 같은 키**가 된다.
- 매퍼 미주입(기본) 시 원문을 `canonical()` 만 걸어 쓴다 → 스모크·개발용.
- 실전에서는 이수연/하경 사전을 매퍼로 넘겨 순위표와 표기 규칙을 일치시킬 것.
