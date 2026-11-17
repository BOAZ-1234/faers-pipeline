# D단계 백테스트 하네스 — v2~v5 인수인계 가이드

정답지(gold)로 신호 순위표(`fct_signal_scores`)를 채점하는 하네스. **v1(IC025 단독) 완료.**
이 문서로 v2~v5를 각자 맡아 (1) 데이터 정제 (2) 하네스 채점한다.

---

## 1. 개요 — 버전 사다리 · 역할

피처를 하나씩 얹으며 순위가 오르는지 본다("한꺼번에 안 만든다").

| 버전 | 추가 피처 | 필요 데이터 | 담당 |
|---|---|---|---|
| v1 | `ic025` | (완료) | ✅ |
| v2 | 시점(PIT) 리드타임 | as_of 분기 스냅샷(이수연) + OpenFDA 조치일(이지훈) | **이수연·이지훈** |
| v3 | `+death_frac` | FAERS OUTC `outc_cod` | **이지훈** |
| v4 | `+dechal_frac, rechal_frac` | FAERS DRUG `dechal`,`rechal` | **이지훈** |
| v5 | `+recency` | FAERS DEMO `fda_dt` | **이지훈** |

**역할**
- **장수연** — 채점기/평가 하네스(`scoring/`) 소유. 백테스트 운영, 랭킹 실험(예: `ic025×log(a)`),
  결과 해석·발표 스토리, 팀원 산출물 통합·검수.
- **이수연** — `fct_signal_scores`(dbt GOLD) 소유. v2 as_of 스냅샷, v3~5 피처 컬럼 통합·export.
- **이지훈** — v3/4/5 피처 집계(OUTC/DRUG/DEMO) + **OpenFDA 조치일 추출** + 정답지 성분 정규화(§5).
- **함하경** — 부작용 사전(`reaction_dict`) 확장(§5 워크리스트 검토).

- v1~v2는 `ic025`로 정렬, **v3~v5는 shrinkage 로지스틱 회귀(numpy)로 가중치 학습** —
  `signals`에 피처 컬럼만 있으면 `build_ranking`이 자동 사용. 피처는 **누적**(v5는 v3·v4 피처 전부 필요).

---

## 2. 조인 키 계약 (노트 ③) — 꼭 읽기

채점기는 **정규화를 하지 않는다.** `canonical()`(소문자·공백·NFKC)만 걸어 완전일치로 조인.
성분/반응 정규화는 **상류(사전)**에서 끝나야 한다.

> ⚠️ **정답지는 아직 미정규화.** gold `product`/`signal`은 FDA 공지 자유텍스트(브랜드·염·클래스)라
> FAERS 성분 정규화 파이프라인을 안 거쳤다. 현재 `scoring/labelmap.py`가 염/철자/괄호를 regex로
> 임시 보정(브릿지). **정식 해법 = 정답지 `product`를 FAERS 성분 정규화기에 태워
> `gold_ingredient_norm` 생성 → 채점기는 canonical만**(§5).

---

## 3. 하네스 실행 (공통)

```bash
# 레포 루트, PYTHONPATH=. (scoring 패키지 import)
PYTHONPATH=. python -m scoring.backtest \
    --signals <fct_signal_scores.parquet> \
    --drug-map <drug_ingredient_map.csv> \
    --reaction-dict <reaction_dict_final.csv> \
    --version v1 --gate
```

| 플래그 | 의미 |
|---|---|
| `--version` | 채점 버전(반복 가능: `--version v1 --version v3`) |
| `--gate` | 표준 게이트 a≥3 & IC025>0 |
| `--include-classes` | 클래스 수준 정답지(NSAIDs 등)도 포함(기본=제외, 성분단위 범위 밖) |
| `--ks "1000,5000,..."` | 평가 K |
| `--out result.json` | JSON 저장 |

**출력 읽는 법:** `커버리지`(정답지를 순위표 키로 올린 비율=Recall 상한, **0이면 사전 미주입**) →
`Recall@K`(상위 K에서 양성 비율) → `Lift@K`(무작위 대비 배수). 결과 해석은 `docs/v1-backtest-results.md`.

---

## 4. v2 — 시점(PIT) 리드타임 (처음부터)

**목표:** "FDA 조치보다 몇 분기 먼저 신호가 떴나" = **리드타임 = 조치분기 − 최초탐지분기** (컨퍼런스 와우포인트).
전기간 합산본으론 불가 — 시점별로 뽑아야 한다.

### 필요 데이터 2종
- **(A) as_of 분기 스냅샷** = 우리가 *언제* 탐지했나. **(이수연)**
- **(B) OpenFDA 조치일** = FDA가 *언제* 조치했나. **(이지훈)**

### 뽑는 법 — 단계별

**A. as_of 스냅샷 → 최초 탐지 분기**
1. 분기 격자 정의(예: 2013Q1 ~ 최신). 분기 인덱스는 선형 `year*4+(분기-1)` (= `scoring/pit.quarter_index`, 뺄셈이 곧 분기 수).
2. 각 분기 `c`마다 **`fct_signal_scores`를 `fda_dt < c` 조건으로 집계** → 그 시점 신호표.
   - ⚠️ **메인 `fct_signal_scores`와 *완전히 동일한 집계 로직*(role·resolution·caseversion)**이어야 함.
     → 이수연이 **dbt 모델에 날짜 컷오프 파라미터만 추가**해 재사용하는 게 가장 안전(별도 raw 재집계 금지:
     카운트 정의가 틀어지면 리드타임 전부 깨짐).
3. 각 분기 스냅샷에서 **게이트(a≥3 & IC025>0) 통과한 (성분,반응)** = 그 시점 탐지집합.
4. 각 (성분,반응)의 **최초 탐지 분기 `first_q`** = 게이트를 **처음** 통과한 분기 `c`.
   → `first_detection.csv` (`ingredient, pt, first_q`) 로 export.

**B. 조치 분기**
5. **OpenFDA drug label API / Drugs@FDA**에서 각 양성 신호의 **라벨 변경·안전성 공지 날짜**를 분기 단위로 추출
   → gold에 `action_q` 컬럼으로 붙임. (지금 `leadtime_report.py`는 gold `first_year`(연)만 써서 거침 — 분기로 정밀화)

**C. 리드타임 계산 (로컬)**
```bash
PYTHONPATH=. python scripts/leadtime_report.py \
    --first-detection first_detection.csv \
    --signals fct_signal_scores.parquet \
    --drug-map drug_ingredient_map.csv \
    --reaction-dict reaction_dict_final.csv \
    --out leadtime_rows.csv
```
→ 출력: **조치 이전 탐지율** + **리드타임(분기) 중앙값**. per-key 표는 `leadtime_rows.csv`.

> 평가 로직(`scoring/pit.py`)·리포트(`leadtime_report.py`)는 완성돼 있음. **남은 건 위 (A)(B) 데이터 생성뿐.**

---

## 5. v3 / v4 / v5 — 피처 컬럼 정제 (이지훈, 병렬 가능)

**산출물 = `fct_signal_scores`에 (성분,반응)쌍별 피처 컬럼 추가.** 컬럼만 생기면 `--version v3` 등 바로 작동.
없으면 `FeatureUnavailable`로 알림(가짜로 안 채움). 요구 컬럼 = `scoring/ranking.py`의 `VERSIONS[vN].features`.

| 피처 | 소스 | 정의 (쌍별) |
|---|---|---|
| `death_frac` (v3) | FAERS OUTC `outc_cod` | 공출현 case 중 `outc_cod='DE'`(사망) 비율 (선택: serious=DE/LT/HO/DS/CA/RI) |
| `dechal_frac` (v4) | FAERS DRUG `dechal` | 성분이 suspect(PS/SS)인 case 중 `dechal='Y'` 비율 |
| `rechal_frac` (v4) | FAERS DRUG `rechal` | 위와 동일, `rechal='Y'` |
| `recency` (v5) | FAERS DEMO `fda_dt` | 최근 2년 내 보고 비율(0~1) 또는 보고분기 가중합 |

정규화는 성분/반응 모두 **메인 신호표와 같은 vocab**으로.

---

## 6. 커버리지 더 올리기

v1 커버리지 **키생성 68.9% / OK존재 59.4%**(클래스 제외). 레버:

1. **정답지 성분 정규화(이지훈)** — `gold.product → gold_ingredient_norm`을 FAERS 성분 정규화기로 생성.
   그러면 염/브랜드/철자 regex 폴백 불필요(노트 ③ 복귀). = 커버리지 올리는 **정식 방법.**
2. **반응 사전 확장(함하경)** — `reaction_dict_final.csv`는 118행. 실패 signal 135건 + 퍼지 제안 =
   `Downloads/reaction_dict_worklist.csv`. `승인PT` 칸만 채워 사전에 append.
   ⚠️ 자동주입 금지(`Renal toxicity→retinal toxicity` 오매칭) — **사람 검토 필수.**
3. **클래스 행** — 성분단위 범위 밖(기본 제외). 넣으려면 클래스→성분목록 사전으로 "구성성분 중 하나라도 탐지"로.

> 참고: **커버리지 ≠ recall.** 커버리지 올려도 recall은 안 오른다(랭킹 문제). 상세 `docs/v1-backtest-results.md`.

---

## 7. 인프라 / 함정

- **EC2 접속 = SSM 세션(대화형)만.** `aws ssm start-session --target <id> --profile faers`.
- 🔑 **[요청] Claude 직접 실행 권한:** 지금 `boaz_3`가 `ssm:SendCommand` **AccessDenied**라 EC2 집계를
  사람이 SSM 웹터미널에 복붙해야 함. **`boaz_3`(또는 전용 IAM)에 `ssm:SendCommand` 또는 SSH 접속을
  열어주면** Claude가 집계·백테스트를 직접 실행(복붙 제거). → AWS 관리자에게 요청. (`ce:GetCostAndUsage`도 막힘)
- **S3 브릿지:** `s3://boaz-1234-825494477740-ap-northeast-2-an/iceberg_warehouse/stage_c_silver/stage3/`
  (`--profile faers`로 read/write). EC2 집계는 리전 내라 egress $0, 로컬에서 S3 끌면 egress 발생.
- **GPU 비용:** g6.xlarge ≈ 시간당 ~$1. **작업 끝나면 반드시 인스턴스 정지.**
- **Windows:** 경로 `C:/...`, 콘솔 UTF-8, 실행 `PYTHONPATH=.`.
- **메모:** `memory/d-backtest-harness-versions.md`, `d-v1-first-backtest-result.md`, `d-v2-pit-leadtime-result.md`.

---

## 8. 체크리스트

- [ ] (이수연) v2 as_of 분기 스냅샷 (dbt에 날짜 컷오프 파라미터, 메인과 동일 로직) → `first_detection.csv`
- [ ] (이수연) v3~5 피처 컬럼을 `fct_signal_scores`에 통합·export
- [ ] (이지훈) v3/4/5 피처 집계: `death_frac`(OUTC)·`dechal_frac`·`rechal_frac`(DRUG)·`recency`(DEMO)
- [ ] (이지훈) OpenFDA 라벨에서 조치 분기 추출 → gold `action_q`
- [ ] (이지훈) 정답지 product 성분 정규화 → `gold_ingredient_norm`
- [ ] (함하경) `reaction_dict_worklist.csv` 승인 → `reaction_dict` 확장
- [ ] (AWS 관리자) `boaz_3`에 `ssm:SendCommand`(또는 SSH) → Claude EC2 직접 실행
- [ ] (장수연) 데이터 들어올 때마다 백테스트 운영 + `ic025×log(a)` 등 랭킹 실험 + 결과 해석·통합
- [ ] (장수연) 각 버전 `PYTHONPATH=. python -m scoring.backtest --version vN --gate` → 커버리지·Lift 확인
