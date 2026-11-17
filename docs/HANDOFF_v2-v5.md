# D단계 백테스트 하네스 — v2~v5 인수인계 가이드

정답지(gold)로 신호 순위표(`fct_signal_scores`)를 채점하는 하네스다. **v1(IC025 단독)은
완료**됐고, 이 문서는 **v2~v5를 각자 맡아** (1) 필요한 데이터를 정제하고 (2) 하네스로
채점하는 방법을 정리한다.

---

## 0. 큰 그림

버전 사다리 — "한꺼번에 안 만든다". 피처를 하나씩 얹으며 순위가 오르는지 본다.

| 버전 | 추가 피처 | 필요 데이터(정제 대상) | 담당 |
|---|---|---|---|
| v1 | `ic025` | (완료) | ✅ |
| v2 | 시점(PIT) 라벨 필터 | as_of 분기 스냅샷 | |
| v3 | `+death_frac` | FAERS **OUTC** `outc_cod` | |
| v4 | `+dechal_frac, rechal_frac` | FAERS **DRUG** `dechal`,`rechal` | |
| v5 | `+recency` | FAERS **DEMO** `fda_dt` | |

- v1~v2는 `ic025`로 정렬. **v3~v5는 shrinkage 로지스틱 회귀(numpy, sklearn無)로 가중치 학습** —
  `signals` 파일에 피처 컬럼만 있으면 `build_ranking`이 자동으로 쓴다.
- 피처는 **누적**이다: v4는 v3의 `death_frac`도 필요, v5는 v3·v4 피처 전부 필요.

### 핵심 계약 (노트 ③) — 꼭 읽기
채점기는 **정규화를 하지 않는다.** `canonical()`(소문자·공백·NFKC)만 걸어 **완전일치**로 조인한다.
성분/반응 정규화는 **상류(사전)**에서 끝나야 한다.

> ⚠️ **정답지는 아직 정규화가 안 돼 있다.** gold의 `product`/`signal`은 FDA 안전성 공지에서
> 긁은 자유텍스트(브랜드·염·클래스·그룹용어)라, FAERS 성분명을 정규화한 **그 파이프라인을
> 안 거쳤다.** 현재는 `scoring/labelmap.py`의 리졸버가 염/철자/괄호를 regex로 임시 보정 중(브릿지).
> **정식 해법 = 정답지 `product`를 FAERS 성분 정규화기에 똑같이 태워 `gold_ingredient_norm`을
> 만들고, 채점기는 canonical로만 조인.** (아래 §5 커버리지)

---

## 1. 공통 입력

| 입력 | 무엇 | 출처 |
|---|---|---|
| `signals` | `fct_signal_scores` export (.parquet/.csv). `ingredient,pt,a,ic025,...` | dbt GOLD build 후 export (이수연) |
| `--drug-map` | `medicinalproduct→ingredient_norm` | 약품 사전(d-2026Qx) |
| `--reaction-dict` | `signal→pt_set_id`(멀티 가능) | 부작용 사전(함하경) |
| `--gold` | 정답지(기본 내장 labelset_gold) | Drugs@FDA |

`signals`는 **EC2에서 export한 파일**을 넘긴다(로컬에서 S3 끌면 egress 발생).

---

## 2. 하네스 실행 (공통)

```bash
# 레포 루트에서. scoring 패키지 import 되게 PYTHONPATH=.
PYTHONPATH=. python -m scoring.backtest \
    --signals <fct_signal_scores.parquet> \
    --drug-map <drug_ingredient_map.csv> \
    --reaction-dict <reaction_dict_final.csv> \
    --version v1 --gate
```

- `--version` 반복 가능: `--version v1 --version v3 ...`
- `--gate` : 표준 게이트(a≥3 & IC025>0) 적용
- `--include-classes` : 정답지 **클래스 수준 행(NSAIDs 등)도 포함**. 기본은 **제외**
  (성분단위 스코어링 범위 밖 → 커버리지 분모에서 뺌). [[scoring/labelmap.py: is_class_level]]
- `--ks "100,500,1000,5000"` : 평가 K
- `--out result.json` : JSON 저장

**출력 읽는 법**
```
라벨셋: 컷오프 2023 | 양성 535/... | 양성 pair 커버리지(키생성) 697/1011 (68.9%) (클래스 142 제외)
...
      K   hits   Recall  Prec  Lift   축소율
```
- **커버리지** = 정답지를 순위표 키로 올릴 수 있는 비율(= Recall 상한). **먼저 확인.** 0이면 사전 미주입.
- **Recall@K / Lift@K** = 상위 K에서 양성 얼마나 잡나 / 무작위 대비 몇 배.
- v1은 Recall이 낮다(IC025 단독=약한 베이스라인). **v3~v5 피처가 올리는 게 사다리의 목적.**

---

## 3. v3 / v4 / v5 — 피처 컬럼 정제 (병렬 가능)

**공통 산출물 = `fct_signal_scores`에 (ingredient, pt)쌍별 피처 컬럼 추가.** 컬럼만 생기면
하네스는 그대로 돈다(`--version v3` 등). 정규화는 성분/반응 모두 **GOLD와 같은 vocab**으로.

### v3 — `death_frac` (중대성)
- 소스: FAERS **OUTC** 파일 `outc_cod` (DE=사망).
- 정의: 각 (ingredient, pt) 공출현 case 중 **`outc_cod='DE'`인 비율** = death_frac (0~1).
  (선택: serious_frac = DE/LT/HO/DS/CA/RI 중 하나라도 있는 비율)
- 집계 grain: case 단위 사망여부 → 쌍별 평균.

### v4 — `dechal_frac`, `rechal_frac` (끊음/재투여)
- 소스: FAERS **DRUG** 파일 `dechal`, `rechal` (Y/N/U).
- 정의: 각 쌍에서 **그 성분이 suspect(PS/SS)인** 공출현 case 중
  `dechal_frac = dechal='Y' 비율`, `rechal_frac = rechal='Y' 비율`.

### v5 — `recency` (최근성)
- 소스: FAERS **DEMO** 파일 `fda_dt`.
- 정의(택1): 최근 2년 내 보고된 공출현 case 비율(0~1) **또는** 보고분기 가중합(최근 ↑).

> v3~v5 피처가 없는 버전을 돌리면 `FeatureUnavailable`로 알린다(가짜로 안 채움).
> `scoring/ranking.py`의 `VERSIONS[vN].features`가 요구 컬럼이다.

---

## 4. v2 — 시점(PIT) 리드타임

"FDA 조치보다 몇 분기 먼저 신호가 떴나" (컨퍼런스 와우포인트). 전기간 합산본으론 불가, **시점별
스냅샷 필요.**

**데이터 정제(핵심):** fct_signal_scores를 **`fda_dt < 기준분기`로 잘라 분기별 집계**한 as_of
스냅샷. ⚠️ **반드시 fct_signal_scores와 *동일한 집계 로직*(role 기준·resolution·caseversion)으로.**
(현재 `scripts/finish_pit.py`가 raw iceberg를 따로 집계했더니 `a` 세는 법이 달라 커버리지가
12/562로 붕괴 — 일관성이 전부다. 자세히: `memory/d-v2-pit-leadtime-result.md`)

**실행 경로:**
1. EC2에서 `scripts/finish_pit.py`(또는 `fix_pit.py`) → `first_detection.csv`(ingredient,pt,first_q), S3 자동 업로드.
2. 로컬에서:
   ```bash
   PYTHONPATH=. python scripts/leadtime_report.py \
       --first-detection first_detection.csv \
       --signals fct_signal_scores.parquet \
       --drug-map drug_ingredient_map.csv \
       --reaction-dict reaction_dict_final.csv \
       --out leadtime_rows.csv
   ```
3. 출력: 조치 이전 탐지율 + 리드타임(분기) 중앙값.

**권장:** raw 재집계 대신 **dbt GOLD가 as_of 스냅샷을 직접 emit**(이수연)하면 일관성 보장.

---

## 5. 커버리지 더 올리기 (공통 레버)

v1 커버리지 **키생성 68.9% / OK존재 59.4%**(클래스 제외). 더 올리려면 **스코어러 땜빵이 아니라
상류 정제**:

1. **정답지를 FAERS 성분 정규화기에 태우기** — `gold.product → gold_ingredient_norm`(FAERS와
   같은 사전 캐스케이드). 그러면 염/브랜드/철자 regex 폴백이 전부 불필요(노트 ③ 복귀).
2. **반응 사전 확장** — `reaction_dict_final.csv`는 118행뿐. 실패 signal 135건을 퍼지 제안과 함께
   뽑아둠: `Downloads/reaction_dict_worklist.csv` (`승인PT` 칸만 채워 사전에 append).
   ⚠️ 자동주입 금지(`Renal toxicity→retinal toxicity` 같은 오매칭) — **사람 검토 필수.**
3. **클래스 행** — 성분단위 범위 밖(기본 제외). 원하면 클래스→성분목록 사전으로 펼쳐 "구성성분 중
   하나라도 탐지"로 채점(설계 결정).

---

## 6. 인프라 / 함정

- **EC2 접속 = SSM 세션(대화형)만.** `aws ssm start-session --target <id> --profile faers`.
  `ssm:SendCommand`(원격 실행)·`ce:GetCostAndUsage`(비용) 권한은 막혀 있음.
- **S3 브릿지:** `s3://boaz-1234-825494477740-ap-northeast-2-an/iceberg_warehouse/stage_c_silver/stage3/`
  (스크립트·결과 올리고 내림, `--profile faers`). 스크립트가 결과 자동 업로드하게 해두면 편함.
- **GPU 비용:** g6.xlarge ≈ 시간당 ~$1. **작업 끝나면 반드시 인스턴스 정지.**
- **Windows:** 경로는 `C:/...`(슬래시), 콘솔 UTF-8. 레포 실행은 `PYTHONPATH=.`.
- **관련 메모:** `memory/d-backtest-harness-versions.md`, `d-v1-first-backtest-result.md`,
  `d-v2-pit-leadtime-result.md`, `cstage-prod-run3-outputs-location.md`.

---

## 7. 체크리스트 (팀원용)

- [ ] (이수연) fct_signal_scores에 `death_frac`·`dechal_frac`·`rechal_frac`·`recency` 컬럼 추가
- [ ] (이수연) v2용 as_of 분기 스냅샷 (fct와 동일 로직)
- [ ] (함하경) `reaction_dict_worklist.csv` 승인 → reaction_dict 확장
- [ ] (상류) 정답지 product를 FAERS 성분 정규화기로 정제 → gold_ingredient_norm
- [ ] 각 버전 `PYTHONPATH=. python -m scoring.backtest --version vN --gate` → 커버리지·Lift 확인
