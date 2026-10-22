# 액션플랜 — FAERS 신호탐지 파이프라인

*기획서 8장·5-1·12장을 실행 체크리스트로 재구성. 새 태스크 지어내지 않음. ★는 기획서에 없어 사람이 정해야 하는 것.*

---

**이번 주 할당** (상세는 1장 · GPU 결정은 §3 GPU 블록)
- [ ] 함하경 — PR #62(Gate 2·3·4) 머지, GPU benchmark 코드 PR 작성, GPU 파일럿·본 실행·최종 모델 결정문
- [ ] 이지훈 — GPU 인스턴스 환경 세팅(Deep Learning AMI, vLLM, S3 role, 자동 종료 스크립트), 본 실행 모니터링
- [ ] 이수연 — B단계 GOLD 집계(가장자리 합·IC025) 구현 — 선행 작업 다 끝남
- [ ] 장수연 — 2019년치 정답지 분기 확인
- [ ] **4인 공통** — `silver/drug_dict/prompts/stage3_judge_{이름}.txt` 각자 작성 (10/22~23)

---

## 1. 이번 주 현황 — 담당자별

**팀 공통**
- [x] 조인키 표준 회의(7개 키 확정) — ingredient_norm 염접미사 27개(초안) 그대로 채택
- [x] C단계 사전 스키마 확정 — drug_ingredient_map(§5-1 `ingredient_set` 포함, 1장 참고), signal_term→pt_set_id(reaction_dict_final.csv) 둘 다 머지 완료
- [x] dbt contract 구현 완료 — 장수연, PR#25 머지 완료 — 성분(`stg_resolution`/`stg_candidate`)·부작용(`stg_reaction_dict`) contract(`dbt build` PASS=14, 위반 케이스 검증도 함)
- [x] ★ dbt contract — 사전 표기 공백 규칙 수정 (PR#53, 이수연) — "문자열에 공백 있으면 FAIL"로 짜여 있던 성분셋 형식 검사가 `zoledronic acid`·`insulin glargine`처럼 성분명 자체에 공백이 있는 정상 값(실측 46.3%, 90,608/195,824행)을 오탐지하던 버그 수정. **2026-10-17 팀 회의에서 확정**: 사전 표기 그대로, 성분 묶음 내부 공백 허용 — `|` 조각 경계의 공백만 형식 위반으로 봄
- [ ] 조인율 품질테이블 — 장수연, **C단계 전체 완료 후 착수**(실데이터 필요)
- [x] openFDA API 키 발급 완료 — 함하경
- [ ] CI 뼈대 — 보류
- [x] ~~S3 → Cloudflare(R2) 데이터 적재 이전~~ — **철회**. 대신 EC2 작업서버 구축으로 결정(PR#36 머지, infra/README.md에 정식 기록). S3 경로는 그대로라 팀 스크립트 수정 불필요
- [x] C단계 캐스케이드 계약 회의(함하경 설계안, Notion) 완료 — 결정 사항:
  - (B) 사전 충돌 시 처리 → **후보 보존**으로 확정
  - (D) normalization 공통 함수 → **이수연의 match.py `normalize_query()` 기반**으로 확정, `silver/drug_dict/normalize.py`로 분리 완료(match.py는 여기서 import). 회귀 없음 확인
  - (C) Stage 2/3 threshold → **추후 확정**(골든셋 나오면 dev/test 분리해서 결정, 원안대로)
  - entity_id는 이수연 확정: normalized_drug_name 기준(raw 대비 7.0% 절감), raw_drug_name은 별도 매핑 테이블로 추적

**이지훈 (A → C 하네스 → GPU 세팅)**
- [x] 2015~2026년치 백필 완료 (2026-08-28 확인)
- [x] A세부3 — 고유 약물명 추출 + openFDA 적재 코드 작성 (PR#10 머지 완료, 9/7)
- [x] A리드 — 파티션·멱등성 설계 완료 (PR#22/#24, `load_manifest` 기반 재시작 안전장치)
- [x] ★ 2004~2014년치 백필 검증 완료 (PR#33) — AERS zip 대조 일치, FAERS 2013Q3·2013Q4 REAC 누락 발견해 백필. `dict_unique_drugs` 535,316→641,097개로 재추출. 리뷰 후속 PR#42로 삭제키 수정
- [x] ★ EC2 작업서버 구축 완료 (PR#36) — 팀 공용 서버·IAM 정책
- [x] 브랜드+용량 재정규화 (PR#50, 후속 PR#55) — BRAND_DOSE 버킷을 1단계 사전에 재조회
- [x] 3단계 하네스 뼈대 (PR#52) — entity 묶기(392,755개, 6.4% 절감)·후보 합치기·LLM 판정·part 저장/재개
- [x] 하네스에 실제 소스 연결 (PR#60) — FAISS·fuzzy 연결 + DiAna dev 채점 + leakage 필터
- [ ] ★ GPU 인스턴스 환경 세팅 — Deep Learning AMI, vLLM, S3 role, Budget 알림, 자동 종료 스크립트 (이번 주, 10/23)
- [ ] ★ 본 실행 모니터링 — Spot 중단 대응·재개 (10/25, 함하경과 공동)
- [ ] ★ `stage3_judge_jihoon.txt` 프롬프트 작성 (10/22~23)

**이수연 (C세부1 → B리드)**
- [x] 사전 4종 통합(openFDA 라벨·NDC·RxNorm·MFDS) → drug_ingredient_map, 195,824쌍 (PR#8·#11)
- [x] 미국식/INN 명명 차이 확인 완료 — DiAna 포함 시 생기던 아티팩트로 판명(DiAna를 정답지로 분리하며 해소)
- [x] `dictionary_version`·`ingredient_set`·`source` 컬럼 추가 완료
- [x] normalize 공통 함수 분리 완료(`silver/drug_dict/normalize.py`)
- [x] ★ DiAna dictionary — "이름 통일 정답지"로 재분류, 사전에서 제외
- [x] `prod_ai` 1단계 매칭 폴백 입력 구현 (PR#30 머지 완료)
- [x] ★ NOS류 패턴 필터 구현 완료 — PR#30 리뷰 대응
- [x] ★ 전체(2004~2026년치) 커버리지 최종 측정 — 사전+prod_ai(NOS 필터 반영) 신고건 **96.73%**/고유명 30.46%, 미해결 445,806개
- [x] ★ Aioli 논문 조사 완료 — 사전 대조 안 하기로 한 결정 재확인
- [x] ★ prod_ai 회수분 진짜 오류 확인 완료 — 회수분 133,566개 전수 감사. 진짜 결함 1건 발견·수정(캐럿 인코딩 버그, PR#46 머지)
- [x] ★ B단계 그레인 결정 4개 확정 + `fct_case_drug_reaction` dbt contract 구현 (PR#53, `dbt build` PASS=20)
- [ ] ★ B단계 GOLD 집계 쿼리(가장자리 합·IC025) 구현 — 이번 주 (4장 참고)
- [ ] ★ `stage3_judge_suyeon.txt` 프롬프트 작성 (10/22~23)

**함하경 (C리드 + C세부3)**
- [x] C세부3 — 부작용 사전 LLM 판정 + 전수 확인 (PR#17 머지, 118/120)
- [x] C리드 — 캐스케이드 설계(Notion) + 계약 회의 완료
- [x] DiAna held-out 정답지 세팅 (PR#44 머지) — 13,652쌍(dev 10,895/test 2,757)
- [x] 3단계 임베딩 검색 + Recall@K + Langfuse (PR#47 머지) — 130,963개 alias, SapBERT·E5 비교해 **SapBERT K=10** 운영 결정
- [x] ★ Stage 3 품질 게이트 — PR #62: alias corpus 복합제 collapse(Gate 2) + 평가 체계 보정(OUT_OF_SCOPE·동의어 매핑, Gate 3) + 하네스 동시성(ThreadPool, Gate 4). Codex 리뷰 반영 완료. **retrieval 상한 재측정: SapBERT∪fuzzy K=10 = 64.5%** (초기 30.6%는 평가 설계 결함)
- [ ] ★ GPU benchmark 코드 PR 작성 — `silver/drug_dict/benchmark.py` (10/22)
- [ ] ★ `stage3_judge_hakyung.txt` 프롬프트 작성 (10/22~23)
- [ ] ★ GPU 파일럿·본 실행·최종 모델 결정문 작성 (10/23~26, §3 GPU 블록)

**장수연 (C세부2 → D세부2)**
- [x] 채점기 골격 (PR#7 머지 완료)
- [x] 정답지 1,715쌍 분할 + 2023년 컷오프 (PR#15 머지 완료)
- [x] dbt contract 구현 완료 (PR#25 머지 완료)
- [x] ★ 라벨 분류 감사 완료 (PR#28) — 2,268행 전수 감사, source_url 재현성 버그도 수정
- [x] ★ C단계 2단계(유사도 매칭) 구현 — PR#43 머지 완료. `variant_designator_mismatch()` 버그 수정 포함
- [x] ★ 3단계 전 게이팅(triage) 구현 — PR#48 머지. `OUT_OF_SCOPE` 14,674개·`BRAND_DOSE`·**UNKNOWN(Z) = 419,674**로 3분할. 빈도 컷오프 곡선: 신고 ≥2건 **147,718건으로 86% 커버 → 운영점 확정**
- [ ] ★ 2019년치 정답지 분기 포함 여부 확인 — 2018 Q1~Q3는 PR#28로 반영 완료(1,715→2,268쌍), 2019년치는 미확인
- [ ] ★ `stage3_judge_suyeon2.txt` 프롬프트 작성 (10/22~23)
- [ ] D단계 본인 리드 항목(랭킹 가중치·MLflow) — C단계 게이트 확정 후

---

## 2. A단계 — 원본 적재 (리드: 이지훈) ✅ 게이트 충족(2004~2026년치)

**게이트**: 전량 적재 + 고유 약물명 리스트 — 2004~2026년치 둘 다 충족(PR#10, PR#33 머지 완료)

- [x] 2015~2026년치 백필 파이프라인 → Iceberg (2026-08-28 완료)
- [x] ★ 2004~2014년치 백필 검증 완료 (PR#33)
- [x] 고유 약물명 추출 코드 작성 (PR#10 머지 완료)
- [ ] Drugs@FDA 시점 라벨 수집 — 게이트엔 불포함(D단계 필터용)
- [x] A리드(이지훈) — 파티션·멱등성 설계 완료 (PR#22/#24)
- [x] `faers_demo/drug/reac`에 `source_zip` 컬럼 추가 (PR#33)
- [ ] faers `load_manifest` 소급 기록 — 백필분(2013Q3·2013Q4 REAC) 외 기존 분기는 기록 없음. 그 전까지 `load_faers_master.py`를 전체 zip 폴더 대상으로 재실행 금지(DRUG/REAC 중복 append됨)
- [ ] A세부2 — 증분 적재·실패 격리 테이블·품질 지표 적재 — 담당 미정

---

## 3. C단계 — 이름 통일 (2개월, 리드: 함하경) ⚠ 임계경로

**게이트**: 정확도 + 커버리지 개선폭 — **아직 end-to-end 측정 전**. 이번 주 GPU 파일럿·본 실행 후 확정

- [x] 리드 — 캐스케이드 설계 + 계약 회의 완료(1장 참고)
- [x] 세부1(이수연) — 사전 4종 통합 → drug_ingredient_map (195,824쌍)
- [x] 세부1(이수연) — `prod_ai` 1단계 폴백 포함 커버리지 측정 — 신고건 **96.73%**, 미해결 445,806개
- [x] 세부2(장수연) — 유사도 매칭(`similarity.py`) 머지 완료 (PR#43)
- [x] Triage(장수연) — 3-bucket 게이팅 완료 (PR#48). **UNKNOWN(Z) = 419,674 → 신고 ≥2건 운영점 147,718 (86% 커버)**
- [x] 브랜드+용량 재정규화(이지훈) — BRAND_DOSE 해결 (PR#50)
- [x] 세부3(함하경) — Stage 3-A alias corpus·FAISS + Langfuse (PR#47)
- [x] 세부3 하네스(이지훈) — 뼈대 + 실제 소스 연결 (PR#52·#60)
- [x] 세부3 품질 게이트(함하경) — PR #62 (Gate 2·3·4). **retrieval 상한 SapBERT∪fuzzy K=10 = 64.5%**
- [ ] GPU 파일럿·본 실행 — 이번 주 (아래 GPU 블록)

### GPU 운영점·결정 (2026-10-22 회의)

- ✅ **운영점**: `n_reports ≥ 2` (147,718개, UNKNOWN 신고 86% 커버)
- ✅ **인스턴스**: A안 — `g6.xlarge` 온디맨드(개발) → `g5.xlarge` Spot(본 실행). B안(AWS Batch)은 "이번 주 안에 결과" 우선으로 보류
- ✅ **AWS Quota 승인 완료**: Running On-Demand G·VT 8 vCPU / Spot 8 vCPU
- ✅ **예산 상한**: 팀 전체 $50 (1인당 ~$12.5 ≈ 1.8만원)
- ✅ **모델 비교**: Qwen 3B / 7B / 14B 전부 실험
- ✅ **프롬프트 비교**: 4인 각자 작성 후 비교

### 평가 기준 (실험 **전에** 선언)

점수 보고 기준 정하면 안 됨. 선언하고 시작한다.

1. `auto_precision ≥ 99%` — 위험한 자동 오매핑 차단
2. 조건 만족 모델 중 `auto_coverage` 최대
3. 성능 차이가 통계적으로 불명확하면 저렴한 모델 선택
4. `invalid_json_rate < 0.5%`
5. 재시도 포함 실패율 < 1%
6. 동률이면 `cost per 1,000` 낮은 설정
7. dev 526개 점 추정치는 **Wilson CI + 오답 건수**를 함께 보고

### 이번 주 역할 분담 (GPU)

| 날짜 | 작업 | 담당 |
|---|---|---|
| 10/22 | PR #62 머지 | 함하경 |
| 10/22 | GPU benchmark 코드 PR 작성 — `silver/drug_dict/benchmark.py` | 함하경 |
| 10/22~23 | 시스템 프롬프트 각자 작성 — `silver/drug_dict/prompts/stage3_judge_{이름}.txt` | **4인 각자** |
| 10/23 | GPU 인스턴스 환경 세팅 | 이지훈 |
| 10/23 | 파일럿 Phase 1 — dev 526개로 모델 3종 × 프롬프트 4종 × K=10/20 비교 → 열등 조합 제거 | 함하경 |
| 10/24 | 파일럿 Phase 2 — 실데이터 1,000건 + concurrency 8/16/32/64 sweep + 5,000건 계층 표본 | 함하경 |
| 10/25 | 모델·프롬프트·동시성 확정 → sealed test → 본 실행 147,718개 | 함하경 + 이지훈 |
| 10/26 | 결과 정리·오류 분해·발표 자료 | 함하경 |

### 프롬프트 작성 가이드 (4인 각자, 10/22~23)

하네스 설계 원칙("1명이 하네스, 3명이 프롬프트" — PR #52)에 따라 각자 다른 시스템 프롬프트를 만들어 비교한다. 파일 이름(stem)이 그대로 `prompt_version` 메타데이터가 돼 결과·Langfuse에 기록됨.

**저장 위치**: `silver/drug_dict/prompts/stage3_judge_{이름}.txt`

**공통 규약**:
- 출력 포맷 고정: `{decision: MATCH|MULTI|NONE, candidate_ids: [...]}` JSON
- 자유 생성 금지 (후보 중에서만 선택)

**각자 변주 지점**:
- 역할 지시: "You are a pharmacist" vs "FAERS data analyst" vs 중립
- few-shot 예시: 0개 vs 2개 vs 5개
- NONE/MULTI 판단 기준: 보수적 vs 적극적
- 후보 rendering: 번호만 vs 성분 설명 포함
- CoT 유도: "step by step" 명시 vs 생략

**실행**: `python stage3_harness.py run ... --prompt silver/drug_dict/prompts/stage3_judge_hakyung.txt`

**비교 범위**: 운영 후보 모델(Qwen 7B) × 프롬프트 4종. dev 526개 × 4 = 2,100건, GPU 1~2시간.

### GPU benchmark 코드 스펙 (함하경, 10/22)

**PR 제목(안)**: `feat(silver): Stage 3 GPU benchmark와 모델 선택 평가 추가`

포함 내용: 실행 명령 (`silver/drug_dict/benchmark.py`), argparse(모델·프롬프트·동시성), GPU runtime 메타 자동 수집(`nvidia-smi`, `vllm --version`, instance type), 품질·처리량 지표 통합, cost per 1,000 자동 계산, 결과 JSON+CSV, Langfuse `run_id` 연결.

run 메타데이터 스키마:
```json
{
  "run_id": "qwen7b-p2-r-sapbert-v2-c32",
  "model": "Qwen2.5-7B-Instruct-AWQ",
  "prompt_version": "p2",
  "retrieval_version": "r-sapbert-v2",
  "candidate_k": 10,
  "instance_type": "g6.xlarge",
  "gpu_name": "NVIDIA L4",
  "concurrency": 32,
  "requests_per_second": 0.0,
  "latency_p95_ms": 0,
  "max_vram_gb": 0.0,
  "candidate_recall": 0.0,
  "judge_accuracy_given_candidate": 0.0,
  "end_to_end_accuracy": 0.0,
  "auto_precision": 0.0,
  "auto_coverage": 0.0,
  "invalid_json_rate": 0.0,
  "cost_per_1000": 0.0
}
```

### 파일럿 실험군

전부 조합하지 않음. dev 526개로 평가 기준 미달 모델 먼저 떨어뜨린다.

| 조건 | 목적 |
|---|---|
| fuzzy + SapBERT rank fusion (LLM 없음) | GPU 없는 baseline |
| Qwen 3B | 가장 저렴한 LLM baseline |
| Qwen 7B AWQ | 현재 주 후보 |
| Qwen 14B AWQ | 7B 부족 시 상한 확인 |
| 프롬프트 4종 (팀원별 작성) | 프롬프트 설계 영향 측정 |
| K=10 vs K=20 | retrieval recall ↔ 입력 토큰 비용 trade-off |

### 최종 결정문 (함하경, 10/26)

평가 기준 통과 모델 중 비용 최소를 선택하고 **왜 이걸 선택했는지** 결정문 작성. LLM이 반드시 이겨야 할 필요는 없음.

좋은 결론의 예시:
> "Qwen 14B는 7B 대비 judge accuracy가 0.8%p 높았지만 신뢰구간이 겹쳤고, 처리량은 42% 낮고 1,000건당 비용은 68% 높았다. 따라서 운영 모델은 7B AWQ로 선택했다."

또는:
> "fuzzy+SapBERT rank fusion과 LLM judge의 성능 차이가 작아, retrieval 증거가 일치하는 사례는 GPU 없이 확정하고 충돌 사례에만 Qwen을 호출하도록 변경했다. 이를 통해 LLM 호출을 61% 줄였다."

---

## 4. B단계 — 펼치고 집계 (1.5개월, 리드: 이수연)

**게이트**: 순위표 v1 완주

- [x] 리드 — 그레인 결정 4개 확정(role_cod·caseversion·부작용 단위·중복 처리, 1장 참고) + `fct_case_drug_reaction` dbt contract 구현(`dbt build` PASS=20)
- [ ] 리드 — GOLD 4칸 집계 + IC025·묶음 그레인으로 뺄셈 유도(가장자리 합 트릭) — **이번 주 작업**
- [ ] 세부1 — 8,400만 행 펼치기 + 성분 조인 + salting (담당 미정 — C단계 인원 합류 전까지 리드가 겸임)
- [ ] 세부2 — 시점 델타 누적 + 재집계 대조
- [ ] 세부3 — 기준 파일·CI 픽스처·구조 검사

---

## 5. D단계 — 순위와 평가 (1.5개월, 리드: 장수연)

**게이트**: Recall@K · Lift · 리드타임 확정

**정답지 품질**
- [x] ★ 라벨 분류 감사 완료 (PR#28) — 2,268행 전수 검토, 자동분류 대비 116행 정정, 정확도 96.8%
- [ ] ★ 2019년치 정답지 분기 포함 여부 확인 — 2018 Q1~Q3는 PR#28로 반영 완료(1,715→2,268쌍), 2019년치는 미확인

- [ ] 리드 — 랭킹 가중치 학습 + MLflow — C단계 게이트 확정 후 착수
- [ ] 세부1 — 게이트·필터 구현 (동의어 묶음 21개 포함)
- [x] 세부2(장수연) — 채점기 골격 (PR#7)
- [x] 세부2(장수연) — 정답지 1,715쌍 분할 + 2023년 컷오프 (PR#15)
- [ ] 세부2(장수연) — 전체 백테스트 — C·B 완료 후
- [ ] 세부3 — dbt 마트·FastAPI·화면 2개
