# D단계 백테스트 — 결과 · 결정 · v2~v5 인수인계

정답지(gold)로 신호 순위표(`fct_signal_scores`)를 채점하는 하네스. **v1(IC025) 완료.**
아래 **결과를 보고 v1에서 멈출지, v2~v5를 더 할지 먼저 결정**한다. 더 하기로 하면 §3~ 가이드대로.

---

## 0. 결과 & 결정 (먼저 읽기)

**v1 성능** (정답지 양성 283개, 랭킹 후보 833,215쌍. 상세 `docs/v1-backtest-results.md`)

| | 희소·심각 신호 | 흔한 신호 |
|---|---|---|
| 예시 | canagliflozin × 당뇨성 케토산증 | montelukast × 우울증 |
| 등수(ic025) | 상위 **0.67%** | 상위 18.7% |
| 등수(ic025×log(a)) | 상위 **0.09%** (#766) | 상위 4.7% |

- **희소하고 심각한 약물 부작용은 매우 잘 잡힘**(상위 0.1~0.7%). **흔한 부작용은 중간대로 묽음**
  (IC025 구조적 한계 — 흔한 반응은 기대값이 커서 불균형이 "겨우" 잡힘).
- 전체 양성 등수 중앙값: ic025 38.8% → **ic025×log(a) 28.3%** (랭킹만 바꿔도 개선, 새 데이터 無).
- 선행연구 vigiRank(피처로 precision ≈2.6배)와는 지표·데이터가 달라 직접비교 불가(느슨한 참고).

**결정 포인트**

| 선택 | 내용 | 비용 |
|---|---|---|
| **A. v1에서 멈춤** | 희소·심각 신호는 이미 top. `ic025×log(a)` 적용하면 흔한신호도 일부 개선. 간단·완성. | 랭킹 코드 1줄 |
| **B. v2~v5 추가** | **v2 = 리드타임**("FDA보다 N분기 먼저 탐지" 와우포인트) + **v3~5 = 흔한신호 끌어올림**(중대성·끊음/재투여·최근성). | 아래 데이터 정제 |

> 추천: **v2(리드타임)는 발표 임팩트가 커서 할 가치 있음.** v3~5는 흔한신호 개선 여지를 보는 실험.
> v1만으로도 "희소·심각 신호 조기포착"은 이미 성립.

---

## 1. 역할

| 담당 | 할 일 |
|---|---|
| **장수연** | 채점기/평가 하네스(`scoring/`) 소유. 백테스트 운영, 랭킹 실험(`ic025×log(a)`), 결과 해석·발표, 통합·검수 |
| **이수연** | `fct_signal_scores`(dbt GOLD) 소유. v2 as_of 스냅샷, v3~5 피처 컬럼 통합·export |
| **이지훈** | v3/4/5 피처 집계(FAERS OUTC/DRUG/DEMO) + 정답지 성분 정규화(§6) |
| **함하경** | 부작용 사전(`reaction_dict`) 확장(§5) |

- v1~v2는 `ic025`로 정렬, **v3~v5는 shrinkage 로지스틱 회귀(numpy)로 가중치 학습** —
  `signals`에 피처 컬럼만 있으면 `build_ranking`이 자동 사용. 피처는 **누적**.

---

## 2. 하네스 실행 (공통)

```bash
PYTHONPATH=. python -m scoring.backtest \
    --signals <fct_signal_scores.parquet> \
    --drug-map <drug_ingredient_map.csv> \
    --reaction-dict <reaction_dict_final.csv> \
    --version v1 --gate
```

| 플래그 | 의미 |
|---|---|
| `--version` | 채점 버전(반복 가능). `--gate` 표준 게이트 a≥3 & IC025>0 |
| `--include-classes` | 클래스 수준 정답지(NSAIDs 등)도 포함(기본=제외, 성분단위 범위 밖) |
| `--ks "1000,5000,..."` / `--out r.json` | 평가 K / JSON 저장 |

출력: `커버리지`(정답지를 순위표 키로 올린 비율=Recall 상한, **0이면 사전 미주입**) → `Recall@K` / `Lift@K`.

**조인 키 계약(노트 ③):** 채점기는 정규화 안 함, `canonical()`(소문자·공백·NFKC)만 걸어 완전일치.
정규화는 상류(사전)에서 끝나야 함. ⚠️ 정답지 `product`/`signal`은 아직 FDA 자유텍스트(미정규화) →
현재 `scoring/labelmap.py`가 염/철자/괄호를 regex로 임시 보정(브릿지). 정식 해법 = §6.

---

## 3. v3 / v4 / v5 — 피처 컬럼 정제 (이지훈, 병렬 가능)

**소스 = FAERS 원본** (S3 iceberg `stage_a_raw/faers_*`, 이미 적재됨 — finish_pit이 읽는 그 소스).
**산출물 = `fct_signal_scores`에 (성분,반응)쌍별 피처 컬럼 추가** → `--version v3` 등 바로 작동.
없으면 `FeatureUnavailable`로 알림. 요구 컬럼 = `scoring/ranking.py`의 `VERSIONS[vN].features`.

| 피처 | FAERS 소스 테이블 | 필드 | 정의 (쌍별) |
|---|---|---|---|
| `death_frac` (v3) | `faers_outc` | `outc_cod` | 공출현 case 중 `outc_cod='DE'`(사망) 비율 (선택: serious=DE/LT/HO/DS/CA/RI) |
| `dechal_frac` (v4) | `faers_drug` | `dechal` | 성분 suspect(PS/SS) case 중 `dechal='Y'` 비율 |
| `rechal_frac` (v4) | `faers_drug` | `rechal` | 위와 동일, `rechal='Y'` |
| `recency` (v5) | `faers_demo` | `fda_dt` | 최근 2년 내 보고 비율(0~1) 또는 보고분기 가중합 |

> ⚠️ `faers_outc` 테이블명은 iceberg 적재 여부 확인 필요(없으면 FAERS OUTC 분기파일 적재 선행).

---

## 4. v2 — 시점(PIT) 리드타임

**목표:** 리드타임 = **조치 분기 − 최초 탐지 분기** ("FDA보다 N분기 먼저 신호가 떴나").

**필요 데이터**
- **(A) as_of 분기 스냅샷 (이수연)** = 우리가 *언제* 탐지했나:
  `fct_signal_scores`를 **`fda_dt < 기준분기`로 잘라 분기별 집계** → 각 분기의 게이트(a≥3&IC025>0)
  통과쌍. ⚠️ **메인 fct와 *완전히 동일한 집계 로직*(role·resolution·caseversion).** dbt에 날짜
  컷오프 파라미터만 추가해 재사용이 가장 안전(별도 raw 재집계 금지 — 카운트 틀어지면 전부 깨짐).
  → 각 쌍의 **최초 게이트 통과 분기 `first_q`** 추출 → `first_detection.csv`.
- **(B) 조치 분기** = FDA가 *언제* 조치했나:
  **이미 정답지에 있음.** `labelset/data/labelset_gold.csv`의 `year`·`q_start`·`quarter_label`이
  FDA가 그 신호를 **FAERS 분기 신호 보고에 올린 분기** = 조치 분기. → 따로 안 받아와도 됨.
  - (선택) 더 엄격히 "실제 라벨 변경일"로 보려면 **openFDA Drug Labeling API**
    (`open.fda.gov/apis/drug/label`, `effective_time` 필드)로 라벨변경 분기 추출. drugsfda(승인정보)는 아님.

**실행 (로컬):**
```bash
PYTHONPATH=. python scripts/leadtime_report.py \
    --first-detection first_detection.csv --signals fct_signal_scores.parquet \
    --drug-map drug_ingredient_map.csv --reaction-dict reaction_dict_final.csv \
    --out leadtime_rows.csv
```
→ 출력: 조치 이전 탐지율 + 리드타임(분기) 중앙값. 평가 로직(`scoring/pit.py`)·리포트는 완성됨.
**남은 건 (A) 스냅샷 생성뿐**(조치일은 정답지에 있음).

---

## 5. 함하경 — 부작용 사전(`reaction_dict`) 확장

### 왜 필요한가
정답지 매칭 실패는 **거의 다 반응(signal)쪽**(성분은 95% 맞음). 정답지 `signal`이 FDA 자유텍스트라
우리 신호표 PT(MedDRA 정식명)와 표기가 달라서다. 이 사전이 **gold signal → PT** 를 번역하는데,
지금 **118행뿐**이라 못 덮는 게 많다. 행을 추가하면 커버리지가 오른다.

### 뭘 하면 되나
**`reaction_dict_final.csv`(함하경이 준 파일)에 행을 직접 추가.** 형식:
```
signal, pt_set_id
```
- `signal` = 정답지(`labelset/data/labelset_gold.csv`, label=양성 행)의 `signal` 값 **그대로**.
- `pt_set_id` = 그 signal에 해당하는 **우리 신호표 PT** (여러 개면 `|`로 구분).
  PT 정식명은 신호표 `fct_signal_scores`의 `pt` 컬럼(= MedDRA PT)에서 골라야 매칭됨.

### 어떤 게 안 맞고 있나 — 실제 예시 (정답지 signal → 넣을 PT)
| 유형 | 정답지 signal (지금 실패) | 추가할 행 (예) |
|---|---|---|
| 그룹용어(여러 PT) | `Serious skin reactions` | `Serious skin reactions, stevens-johnson syndrome\|toxic epidermal necrolysis\|drug reaction with eosinophilia and systemic symptoms` |
| 동의어 | `Liver failure` | `Liver failure, hepatic failure` |
| 표현차 | `QT prolongation` | `QT prolongation, electrocardiogram qt prolonged` |
| 범주어 | `Cardiopulmonary reactions` | (해당 PT들 판단해 매핑) |
| 서술형 | `Overdose due to labeling confusion` | `Overdose due to labeling confusion, overdose\|product label confusion` |

→ 실패 signal 전체 목록은 장수연이 뽑아 전달. 각 행에 올바른 PT만 채우면 됨.
⚠️ **자동 유사도 매칭 금지** — `Renal toxicity → retinal toxicity`(신장↔망막) 같은 오매칭으로 정답지
오염됨. **반드시 의학적으로 맞는 PT를 사람이 판단.**

> 참고: 이건 **반응쪽 커버리지**를 올린다. recall(순위 정확도)은 이걸로 안 오른다(그건 §0 랭킹/피처).

---

## 6. 커버리지 — 정식 해법 (정답지 성분 정규화, 이지훈)

현재 `drug_ingredient_map` + 리졸버가 정답지 `product`를 성분으로 **임시 번역** 중. 정식 해법 =
**정답지 `product`를 FAERS 성분 정규화기(= `fct_signal_scores.ingredient`를 만든 그 파이프라인)에
똑같이 태워 `gold_ingredient_norm` 생성** → 채점기는 canonical로만 조인(노트 ③ 복귀).
(성분은 이미 95% 맞아 우선순위 낮음. 반응=§5가 더 큰 레버.)

클래스 수준 정답지(NSAIDs 등)는 성분단위 범위 밖(기본 제외). 넣으려면 클래스→성분목록 사전 필요.

---

## 7. 인프라 / 함정

- **EC2 접속 = SSM 세션(대화형)만.** `aws ssm start-session --target <id> --profile faers`.
- 🔑 **[요청] Claude 직접 실행 권한:** `boaz_3`가 `ssm:SendCommand` **AccessDenied**라 EC2 집계를
  사람이 복붙해야 함. **`boaz_3`에 `ssm:SendCommand`(또는 SSH)를 열어주면** Claude가 직접 실행(복붙 제거).
  → AWS 관리자 요청. (`ce:GetCostAndUsage`도 막힘)
- **S3 브릿지:** `s3://boaz-1234-825494477740-ap-northeast-2-an/iceberg_warehouse/stage_c_silver/stage3/`
  (`--profile faers`). EC2 집계는 리전 내라 egress $0, 로컬에서 S3 끌면 egress 발생.
- **GPU 비용:** g6.xlarge ≈ 시간당 ~$1. **작업 끝나면 반드시 인스턴스 정지.**
- **Windows:** 경로 `C:/...`, 콘솔 UTF-8, 실행 `PYTHONPATH=.`.
- **메모:** `memory/d-backtest-harness-versions.md`, `d-v1-first-backtest-result.md`, `d-v2-pit-leadtime-result.md`.

---

## 8. 체크리스트

**결정:** [ ] v1에서 멈춤 / [ ] v2~v5 진행 (§0)

진행 시:
- [ ] (장수연) `ic025×log(a)`를 `scoring/ranking.py` 랭킹 옵션으로 추가 (검증된 개선)
- [ ] (이수연) v2 as_of 분기 스냅샷 (dbt 날짜 컷오프, 메인과 동일 로직) → `first_detection.csv`
- [ ] (이수연) v3~5 피처 컬럼을 `fct_signal_scores`에 통합·export
- [ ] (이지훈) v3/4/5 피처 집계: `death_frac`(OUTC)·`dechal/rechal_frac`(DRUG)·`recency`(DEMO)
- [ ] (이지훈) 정답지 product 성분 정규화 → `gold_ingredient_norm`
- [ ] (함하경) `reaction_dict_final.csv`에 실패 signal→PT 행 추가 (§5)
- [ ] (AWS 관리자) `boaz_3`에 `ssm:SendCommand`(또는 SSH) → Claude EC2 직접 실행
- [ ] (장수연) 데이터 들어올 때마다 `python -m scoring.backtest --version vN --gate` → 커버리지·Lift 확인
