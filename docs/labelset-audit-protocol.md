# 정답지 라벨 분류 감사 프로토콜

*담당: 장수연 (D세부2). action-plan 5장 "정답지 품질", 기획서 12장.*
*채점기의 정답 기준(ground truth)이라 D단계 착수 전에 처리한다.*

## 왜 하나

정답지의 양성/음성/보류 라벨은 하경 0단계 `classify_signals`(`_probe/classify_signals.py`)가
FDA 신호보고서 `info` 텍스트에 **키워드 규칙**으로 자동으로 붙였다. 한 `info` 블록에
여러 제품의 상반된 결과가 섞이면(예: 성분 5종을 나열한 뒤 일부만 라벨 업데이트,
나머지는 "no action") 코드가 **제품명의 문장 내 위치**로 기계 귀속하므로 오분류가 난다.
이 라벨이 틀리면 Recall@K·Lift가 통째로 오염되므로, 위험군을 사람이 대조한다.

실제 사례 (감사 워크시트 block `12d18a66`):

| product | signal | auto_label |
|---|---|---|
| Hydroxychloroquine sulfate | Neuropsychiatric symptoms | 양성 |
| Plaquenil (hydroxychloroquine sulfate) | Neuropsychiatric symptoms | 양성 |
| Primaquine (primaquine phosphate) | Neuropsychiatric symptoms | 음성 |

→ 같은 info 한 문단인데 성분별로 라벨이 갈렸다. 사람이 원문과 대조해 확인해야 한다.

## ★ "200건"의 근거 — 임의 숫자를 census 로 대체

기획서의 "200건"은 근거(실측 vs 임의)가 명시돼 있지 않았다. 실데이터로 재측정한 결과,
**임의 표본이 아니라 위험군 전수(census)** 로 감사 범위를 정의한다:

| 구분 | 정의 | 건수(2,268쌍 기준) |
|---|---|---|
| **대조필수** | `info` 에 조치(updated/revised/boxed/withdr/safety communication/warning letter)와 불요(no action/not associated/no regulatory)가 **동시** 등장 → 제품별 귀속 필요 | **335** (전수) |
| **표본** | 비혼합건 무작위 추출 (기저율·명백건 오분류 점검) | 100 (시드 20260915) |
| 합계 | 감사 워크시트 총 행 | **435** |

- "200"보다 큰 335건이 실제 위험군이다. 무작위 200건은 대부분 명백건이라 헛수고가 되므로,
  **혼합건 전수 + 비혼합 표본** 이 더 방어 가능한 설계다.
- 335 중 387행(표본 포함)이 다른 제품과 같은 info 블록을 공유하는 다제품 케이스다.
- 정답지가 갱신되면(2018·2019 분기 복구로 1,715→2,268 이미 증가) 숫자는 다시 측정한다.
  임계값은 코드가 아니라 데이터가 정한다.

## 절차

1. 워크시트 생성
   ```bash
   python -m scoring.audit_labelset          # → _probe/out/labelset_audit.csv
   python -m scoring.audit_labelset --sample 150   # 표본 크기 조정
   ```
   워크시트는 **대조필수 먼저**, 그 안에서 **같은 `block_id`(info 블록)의 여러 제품이
   인접**하도록 정렬된다. 한 블록을 한 번 읽고 거기 걸린 제품들을 한꺼번에 대조하면 된다.

2. 사람이 채우는 칸
   - `human_label` — 원문(`info`, 필요시 `source_url` 원본) 기준 실제 라벨 (양성/음성/보류)
   - `match` — 자동(`auto_label`)과 일치하면 Y, 아니면 N
   - `note` — 오분류 근거·이견·애매한 사유

3. 결과 집계 — 오분류율(= N 비율)을 대조필수/표본 각각 보고. 오분류 건은 원본 정답지에 정정
   반영하고, 반복 패턴이면 하경 `classify_signals` 규칙 개선 항목으로 넘긴다.

## 컬럼 (labelset_audit.csv)

| 컬럼 | 뜻 |
|---|---|
| `audit_id` | 감사 행 번호 |
| `priority` | 대조필수 / 표본 |
| `block_id` | 같은 `info` 원문 = 같은 id (다제품 대조용 정렬 키) |
| `n_pairs_in_block` | 이 info 블록을 공유하는 쌍 수 (>1 = 다제품 섞임 지점) |
| `product`, `signal` | 정답지 쌍 |
| `auto_label` | 자동 분류 결과 |
| `human_label`, `match`, `note` | ← 사람이 채움 |
| `source_url` | 원본 보고서(분기 페이지). 주의: 분기당 100+쌍이 공유하므로 그룹 키로 못 씀 |
| `info` | 판정 근거 원문 |

## 데이터 보존 주의

`_probe/`, `out/`, `*.csv` 는 `.gitignore` 대상이라 정답지·워크시트는 git 에 안 들어간다.
감사 도구(`scoring/audit_labelset.py`)와 이 문서만 추적된다. 채워진 워크시트는 팀 공유
저장소(예: R2/드라이브)에 별도 보존할 것 — 로컬 삭제로 유실하지 않도록.
