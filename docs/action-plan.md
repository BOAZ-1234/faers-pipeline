# 액션플랜 — FAERS 신호탐지 파이프라인

*기획서 8장·5-1·12장을 실행 체크리스트로 재구성. 새 태스크 지어내지 않음. ★는 기획서에 없어 사람이 정해야 하는 것.*

---

<<<<<<< Updated upstream
**이번 주 할당** (상세는 1장)
- [ ] 이지훈 — 3단계 하네스(PR#52)에 실제 후보 소스 연결: `CandidateSource`에 #47(FAISS 임베딩 검색)·#43(`match_one` fuzzy 매칭) 연결. 지금은 `FixtureSource`(가짜 JSON)뿐이라, 이게 끝나야 아래 프롬프트 비교가 의미를 가짐 — **선행 작업**
- [ ] 함하경 — GPU 사용 방식·예상 비용 조사. 이지훈 연결 끝나면 프롬프트 버전 작성(`stage3_judge_v2.txt` 등) + 비교
- [ ] 장수연 — 운영점(신고건수 컷오프) 결정 — triage(PR#48) 빈도 곡선 기준, GPU 예산(함하경 조사)과 맞춰서. 이지훈 연결 끝나면 프롬프트 버전 작성 + 비교
- [ ] 이수연 — B단계 GOLD 집계 쿼리(가장자리 합 트릭·IC025) 구현 우선. 여유 있으면 프롬프트 버전 작성 + 비교
=======
**이번 주 할당** (상세는 1장 · GPU 결정은 §3-GPU)
- [ ] 함하경 — PR #62(Gate 2·3·4) 리뷰 반영 후 머지, GPU benchmark 코드 PR 작성, dev 526 → 실데이터 1,000 → 5,000건 모델 비교 실험, 최종 모델 선택 결정문 작성
- [ ] 이지훈 — GPU 인스턴스 환경 세팅(Deep Learning AMI, vLLM, S3 role), 자동 종료 스크립트, 본 실행 모니터링 공동 참여
- [ ] 이수연 — Stage 3 결과가 B단계 집계에 미치는 영향 테스트(ingredient_set 조인 재검증), prod_ai 회수분 오류 확인
- [ ] 장수연 — Stage 3 결과가 D단계 채점에 미치는 영향 테스트(unmatched 축소로 Recall@K 재측정), 2019년치 정답지 분기 확인
>>>>>>> Stashed changes

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
  - (A) 스키마 / (E) leakage 방지 — 결과 미확인, 확인 필요

**이지훈 (A → C 하네스)**
- [x] 2015~2026년치 백필 완료 (2026-08-28 확인)
- [x] A세부3 — 고유 약물명 추출 + openFDA 적재 코드 작성 (PR#10 머지 완료, 9/7)
- [x] A리드 — 파티션·멱등성 설계 완료 (PR#22/#24 머지) — `load_manifest` 테이블로 (pipeline, source_zip, file_type) 단위 완료 여부 추적해 재시작 안전장치 마련. DRUG/REAC append 중복, zip 출처 추적 불가, AERS 원본 컬럼 오프셋(끝에 `$` 하나 더 있어 격리 테이블로 새던 것) 등 실전 버그 발견해서 수정, 테스트 코드도 추가
- [x] ★ 2004~2014년치 백필 검증 완료 (PR#33) — AERS(2004Q1~2012Q3) zip 대조 일치, FAERS 2013Q3 전체·2013Q4 REAC 누락 발견해 백필. `dict_unique_drugs` 535,316→641,097개로 재추출(이수연 재검증 필요), `source_zip` 컬럼 추가
- [x] ★ EC2 작업서버 구축 완료 (PR#36) — 팀 공용 서버·IAM 정책, S3→R2 이전은 철회 확정 문서화
- [x] ★ PR#33 리뷰 후속 완료 (PR#42, 9/28) — 삭제 키를 `primaryid`→`source_zip`으로 수정. 실제 겹친 11건(2020Q3 등) 원본 대조 결과 완전 동일 재제출이라 데이터 손실은 없었음(결과가 안전했을 뿐 방식이 안전한 건 아니었다고 PR에 명시)
<<<<<<< Updated upstream
- [x] A 완료 → C단계 3단계 합류 완료
- [x] 브랜드+용량 재정규화 — BRAND_DOSE 버킷을 1단계 사전에 재조회 (PR#50 머지 완료). 리뷰에서 발견된 사전 경로·버전 검사 미흡 후속 패치 완료(PR#55)
- [x] 3단계 하네스 뼈대 구현 (PR#52 머지 완료) — entity 묶기(392,755개, 6.4% 절감)·후보 합치기·LLM 판정·part 저장/재개. `pytest` 21개 통과, triage UNKNOWN(419,674) 기준 실데이터 스모크 완료(가짜 부품으로만). 프롬프트 1개(`stage3_judge_v1.txt`) 직접 작성
- [ ] ★ 하네스에 실제 후보 소스 연결 — `CandidateSource`에 #47 FAISS·#43 `match_one` 연결(지금은 `FixtureSource`뿐). 아래 프롬프트 비교의 선행 조건(1장 참고)

**이수연 (C세부1, B리드)**
=======
- [x] C 하네스 뼈대 — PR#52 머지 완료 (3단계 실험대: 후보 합치기·LLM 판정·저장·재개)
- [x] C 하네스 실제 소스 연결 — PR#60 머지 완료 (FAISS·fuzzy 연결 + DiAna dev 채점 + leakage 필터)
- [ ] ★ GPU 인스턴스 환경 세팅 — Deep Learning AMI, vLLM, S3 role, Budget 알림 + 자동 종료 스크립트 (이번 주, §3 GPU 블록)
- [ ] ★ 본 실행 모니터링 — Spot 중단 대응·재개 (이번 주, 함하경과 공동)
- [ ] ★ `stage3_judge_jihoon.txt` 프롬프트 작성 (이번 주, §3 프롬프트 가이드)

**이수연 (C세부1 → B리드)**
>>>>>>> Stashed changes
- [x] 사전 4종 통합(openFDA 라벨·NDC·RxNorm·MFDS) → drug_ingredient_map, 195,824쌍 (PR#8·#11, 상세는 c-detail1-progress.html)
  - RxNorm "동의어"(오타·철자변형) — **C세부2로 이관**
- [x] 미국식/INN 명명 차이 확인 완료 — DiAna 포함 시 생기던 아티팩트로 판명(DiAna를 정답지로 분리하며 해소). 별도로 "성분 리스트 누락"(774건, ingredient_set 후보 불완전) 발견 — c-detail1-progress.html §16
- [x] `dictionary_version`·`ingredient_set`(§5-1 표준)·`source` 컬럼 추가 완료
- [x] normalize 공통 함수 분리 완료(`silver/drug_dict/normalize.py`, 캐스케이드 계약 회의 안건 D)
- [x] ★ DiAna dictionary — 사전 소스 아니라 **"이름 통일 정답지"**(§7 prod_ai와 동일 역할)로 구두 합의됐던 걸 뒤늦게 확인, PR#26 머지 전 병합 되돌림. `sources/diana.py`는 유지 — held-out 정답지 세팅에 재사용
<<<<<<< Updated upstream
- [x] `prod_ai` 1단계 매칭 폴백 입력 구현 (PR#30 머지 완료)
- [x] ★ NOS류 패턴 필터 구현 완료 — PR#30 리뷰 대응(사전 대조 대신 채택), `PROD_AI_EXCLUDE`에 `\bNOS\b` 정규식 + `MINERALS`·`VITAMIN B` 추가. 단위테스트 11개 + 실측 검증
- [x] ★ 전체(2004~2026년치) 커버리지 최종 측정 — 고유 약물명 641,097개, 사전만 신고건 84.69%/고유명 9.63%, 사전+prod_ai(NOS 필터 반영) 신고건 **96.73%**/고유명 30.46%, 미해결 445,806개. `unmatched_for_stage2.csv` 릴리즈 아카이빙 완료(장수연 2단계 입력용)
- [x] ★ Aioli 논문(PMC12411121) 조사 완료 — Aioli는 사전 대조를 하는데 우리는 왜 안 하기로 했는지 확인. 답: Aioli 사전(RxNorm+Extension+EU Article 57+Orange Book, 전체 동의어 포함)이 우리 4종보다 훨씬 크고, 그래도 못 잡는 공백은 수동 큐레이션 리스트(422개)로 메움. 우리 사전이 작아서 사전 대조를 안 하기로 한 결정 자체는 맞았음. 재검토 시 참고할 기법: `findRxcuiByString`(RxNorm 정규화 API, 퍼지매칭 아님) — PR#43 유사도 매칭 리뷰에 제안할 만한 후보로 남겨둠(b-stage-progress.html 등에는 미반영, 별도 메모)
- [x] ★ prod_ai 회수분 진짜 오류(오타 등) 확인 완료 — 회수분 133,566개(신고 1,018만 건) 전수 감사: exact 87.39%/near(오타 의심) 3.96%/novel(사전 공백) 8.65%. near 상위권은 대부분 오타가 아니라 "철자가 닮은 다른 진짜 약"(rosiglitazone/pioglitazone 등) — 사전 대조 안 하기로 한 결정 재확인. 진짜 결함 1건 발견·수정: prod_ai에서 캐럿(`^`)이 아포스트로피 자리에 깨져 들어오는 인코딩 버그(30개 고유명·3,518건, 회수분의 0.03%) → `normalize.py` 수정(PR#46 머지 완료)
- [x] ★ B단계 그레인 결정 4개 확정 + dbt contract 구현 — 실측 기반으로 role_cod(PS+SS만, C/I 제외 — 전체 drug 행의 41.8% 영향)·caseversion(case당 최신판만, `caseversion`·`fda_dt` 같이 보존)·부작용(FAERS `pt` 원형 그대로, 변환 없음)·중복 ingredient_set(`COUNT(DISTINCT caseid)`) 4개 결정. 팀 공유 완료(영향 큰 1·2번). `fct_case_drug_reaction` dbt contract(`contracts/dbt/models/b_stage/`) 구현, `dbt build` **PASS=20** 확인, 깨보기 검증도 완료. `role_cod="DN"`(881건, 0.00%) 의미는 미확인으로 남김. PR#53에서 사전 표기 공백 버그(C단계 쪽과 동일 패턴)도 같이 발견해 수정
- [ ] ★ B단계 — GOLD 집계 쿼리(가장자리 합 트릭·IC025) 구현 — 다음 작업 (4장 참고)
=======
- [x] `prod_ai` 1단계 매칭 폴백 입력 구현 (PR#30 머지)
- [x] ★ NOS류 패턴 필터 구현 완료 — PR#30 리뷰 대응(사전 대조 대신 채택), `PROD_AI_EXCLUDE`에 `\bNOS\b` 정규식 + `MINERALS`·`VITAMIN B` 추가. 단위테스트 11개 + 실측 검증
- [x] ★ 전체(2004~2026년치) 커버리지 최종 측정 — 고유 약물명 641,097개, 사전만 신고건 84.69%/고유명 9.63%, 사전+prod_ai(NOS 필터 반영) 신고건 **96.73%**/고유명 30.46%, 미해결 445,806개. `unmatched_for_stage2.csv` 릴리즈 아카이빙 완료(2단계 입력용)
- [x] ★ Aioli 논문(PMC12411121) 조사 완료 — Aioli는 사전 대조를 하는데 우리는 왜 안 하기로 했는지 확인. 답: Aioli 사전(RxNorm+Extension+EU Article 57+Orange Book, 전체 동의어 포함)이 우리 4종보다 훨씬 크고, 그래도 못 잡는 공백은 수동 큐레이션 리스트(422개)로 메움. 우리 사전이 작아서 사전 대조를 안 하기로 한 결정 자체는 맞았음 — 사전을 키우거나(DailyMed·Purple Book 등) 수동 리스트를 만들면 재검토 가능
- [x] ★ Triage 분기 구현 완료 — PR#48(3-bucket 게이팅: OUT_OF_SCOPE·BRAND_DOSE·UNKNOWN), PR#50(renormalize), PR#57(triage↔renormalize 배선)
- [ ] ★ prod_ai 회수분 진짜 오류(오타 등) 확인 — 사전 대조 대신 채택. 이번 주 진행
- [ ] ★ Stage 3 결과 → B단계 조인 영향 테스트 (ingredient_set 재검증, 이번 주, §3 GPU 블록)
- [ ] ★ `stage3_judge_suyeon.txt` 프롬프트 작성 (이번 주, §3 프롬프트 가이드)
- [ ] ★ B단계 틀 잡기 — Stage 3 완료 후 착수 (4장 참고)
>>>>>>> Stashed changes

**함하경 (C리드 + C세부3)**
- [x] C세부3 3단계 — LLM 판정 + 전수 확인 (PR#17 머지 완료, 118/120 매핑, 부작용 사전)
- [x] C리드 — 캐스케이드 설계(Notion) + 계약 회의 완료(팀공통 항목 참고)
<<<<<<< Updated upstream
- [x] DiAna held-out 정답지 생성 스크립트 — PR#44 머지 완료. `fetch_counts.py`를 `coverage.py --out-freq-csv`로 통합, 리뷰 중 나온 `unsafe_enable_version_guessing` 질문도 확인 완료(버전힌트 파일 있으면 그걸 우선 쓰는 폴백이라 안전, 실제 S3 버킷에도 힌트 파일 존재 확인)
- [x] Langfuse 세팅 완료 (PR#47) — `langfuse_tracer.py`, Trace=raw_drug_name·Generation=llm-judge 구조
- [x] 3단계 임베딩 검색 구현 (PR#47 머지 완료) — FAISS(`IndexFlatIP`) + Recall@K 측정 스크립트. 리뷰에서 FAISS `-1` 패딩을 그대로 `corpus[-1]`로 인덱싱하던 버그(작은 인덱스에서 Recall 수치 조용히 오염) 발견해 수정, `dictionary_version` 하드코딩도 `build_map.py`에서 import하도록 수정 + 버전 불일치 시 에러 체크 추가. 후속 패치(PR#56): `UNMATCHED_DEFAULT` 변수를 만들어놓고 `--unmatched` 플래그 기본값에 연결을 안 해서 플래그 없이 돌리면 leakage 방지가 꺼진 채 실행되던 버그 수정
- [ ] 3단계 LLM 판정(드러그명) — 하네스(PR#52)엔 `MockJudge`만 연결·테스트됨, 실제 vLLM 엔드포인트 연결은 미착수 (이지훈의 `CandidateSource` 연결 후)
- [ ] ★ GPU 사용 방식·예상 비용 조사 — Z(UNKNOWN)=419,674(아래 3장 참고)가 입력값. 이번 주 진행
- [ ] ★ 프롬프트 버전 작성 + 비교 — 지금 `stage3_judge_v1.txt`(이지훈 작성) 1개뿐, "3명이 각자 써서 비교"하기로 한 계획 중 1/3만 진행됨. 하네스 연결 후 착수
=======
- [x] ★ DiAna held-out 정답지 세팅 — PR#44 머지 완료. 원본 45만 쌍 → 신고 ≥200건 필터 → 13,652쌍(dev 10,895/test 2,757, seed=42)
- [x] ★ Stage 3-A alias corpus + FAISS 구축 완료 — PR#47 머지. 130,963개 alias, SapBERT·E5 두 모델 비교해 **SapBERT K=10** 운영 결정
- [x] ★ Langfuse 연결 완료 — `langfuse_tracer.py` 머지. Trace 1건당 메타(dictionary·retrieval·prompt·llm_model·routing_reason) 기록. 실제 trace는 GPU 돌릴 때부터 쌓임
- [x] ★ Stage 3 품질 게이트 완료 — PR #62: alias corpus 복합제 collapse 수정(Gate 2), 평가 체계 보정(OUT_OF_SCOPE·동의어 매핑, Gate 3), 하네스 동시성(ThreadPool, Gate 4). Codex 리뷰 반영 완료. **retrieval 상한 재측정: SapBERT∪fuzzy K=10 = 64.5%** (초기 30.6%는 평가 설계 결함)
- [ ] ★ GPU benchmark 코드 PR 작성 — `silver/drug_dict/benchmark.py` (이번 주, §3 GPU 블록)
- [ ] ★ `stage3_judge_hakyung.txt` 프롬프트 작성 (이번 주, §3 프롬프트 가이드)
- [ ] ★ GPU 파일럿·본 실행·최종 모델 결정문 작성 (이번 주, §3 GPU 블록)
>>>>>>> Stashed changes

**장수연 (C세부2 → D세부2)**
- [x] 채점기 골격(완전일치매칭·Recall@K·Lift·검토대상축소율·스모크) (PR#7 머지 완료)
- [x] 정답지 1,715쌍 분할 + 2023년 컷오프 (PR#15 머지 완료)
<<<<<<< Updated upstream
- [ ] dbt contract 구현 완료(PR#25 머지 완료) + 조인율 품질테이블은 아직
- [x] ★ 라벨 분류 감사 완료 (PR#28) — 200건 표본 대신 2,268행 전수 감사, source_url 재현성 버그도 같이 수정. 상세는 4장
- [ ] ★ 2019년치 정답지 분기 포함 여부 확인 — PR#28로 2018 Q1~Q3 복구분(1,715→2,268쌍)은 반영 완료, 2019년치는 미확인
- [x] ★ C단계 2단계(유사도 매칭) 구현 — PR#43 머지 완료. 리뷰에서 발견된 `variant_designator_mismatch()` 버그(로마숫자·문자+숫자 지정자 양쪽 다 부분문자열 경계 문제 — `VITAMIN B12`→`VITAMIN B1`, `FACTOR VIII`→`FACTOR VI` 오매칭 실측 확인) 수정 완료
- [x] ★ 3단계 전 게이팅(triage) + 규모 산정 — PR#48 머지 완료. `OUT_OF_SCOPE` 14,674개(신고 27.0%)·`BRAND_DOSE`(이지훈 PR#50으로 해결)·**UNKNOWN(Z) = 419,674**로 3분할. 빈도 컷오프 곡선 계산: 신고 ≥2건이면 호출 147,718건으로 86% 커버. 비타민/화장품류는 과제거 없게 안전장치(경계 테스트 포함)
- [ ] ★ 운영점(신고건수 컷오프) 결정 — triage 곡선(≥2건 147,718 / ≥3건 86,130 / ≥10건 24,195 / ≥50건 3,978) 중 실제 운영에 쓸 지점. GPU 예산(함하경 조사)과 맞춰서 확정. 이번 주 진행
- [ ] ★ 프롬프트 버전 작성 + 비교 — 하네스 연결 후 착수(함하경과 동일)
- [ ] D단계 본인 리드 항목(랭킹 가중치·MLflow)은 아직 별도로 착수 전
=======
- [x] dbt contract 구현 완료 (PR#25 머지 완료)
- [x] ★ 라벨 분류 감사 완료 (PR#28) — 200건 표본 대신 2,268행 전수 감사, source_url 재현성 버그도 같이 수정. 상세는 5장
- [x] ★ C단계 2단계(유사도 매칭) 구현 완료 — PR#43 머지. `variant_designator_mismatch()` 버그 수정 포함(TAB12 ↔ B12 오탐지)
- [ ] ★ 조인율 품질테이블 — Stage 3 결과 나오면 착수(실데이터 필요)
- [ ] ★ 2019년치 정답지 분기 포함 여부 확인 — PR#28로 2018 Q1~Q3 복구분(1,715→2,268쌍) 반영 완료, 2019년치는 미확인
- [ ] ★ Stage 3 결과 → D단계 Recall@K 재측정 (unmatched 축소 영향, 이번 주, §3 GPU 블록)
- [ ] ★ `stage3_judge_suyeon2.txt` 프롬프트 작성 (이번 주, §3 프롬프트 가이드)
- [ ] D단계 본인 리드 항목(랭킹 가중치·MLflow) — Stage 3·B단계 완료 후
>>>>>>> Stashed changes

---

## 2. A단계 — 원본 적재 (리드: 이지훈) ✅ 게이트 충족(2004~2026년치)

**게이트**: 전량 적재 + 고유 약물명 리스트 — 2004~2026년치 둘 다 충족(PR#10, PR#33 머지 완료). `dict_unique_drugs`는 641,097개로 재추출(이전 535,316개 버전은 이수연 set-equal 검증 완료 — 새 버전 재검증 필요, 3장 참고)

- [x] 2015~2026년치 백필 파이프라인 → Iceberg (2026-08-28 완료)
- [x] ★ 2004~2014년치 백필 검증 완료 (PR#33) — 상세·리뷰 후속 항목은 3장
- [x] 고유 약물명 추출 코드 작성 (PR#10 머지 완료)
- [ ] Drugs@FDA 시점 라벨 수집 — 게이트엔 불포함(D단계 필터용, 별도 항목). PR#10에 포함 여부 확인 필요
- [x] A리드(이지훈) — 파티션·멱등성 설계 완료 (PR#22/#24, `load_manifest` 기반 재시작 안전장치)
- [x] `faers_demo/drug/reac`에 `source_zip` 컬럼 추가 (PR#33, 기존 행은 NULL)
- [ ] faers `load_manifest` 소급 기록 — 백필분(2013Q3·2013Q4 REAC) 외 기존 분기는 기록 없음. 그 전까지 `load_faers_master.py`를 전체 zip 폴더 대상으로 재실행 금지(DRUG/REAC 중복 append됨)
- [ ] A세부2 — 증분 적재·실패 격리 테이블·품질 지표 적재 — 담당 미정

---

## 3. C단계 — 이름 통일 (2개월, 리드: 함하경) ⚠ 임계경로

**게이트**: 정확도 + 커버리지 개선폭 — **아직 측정 전**(아래 캐스케이드가 실제 컴포넌트로 안 연결돼서 end-to-end 수치가 아직 없음)

- [x] 리드 — 캐스케이드 설계 + 계약 회의 완료(1장 참고)
- [x] 세부1(이수연) — 사전 4종(openFDA·NDC·RxNorm·MFDS) 통합 → drug_ingredient_map (195,824쌍, 1장 참고. DiAna는 정답지로 재분류돼 사전에서 제외)
<<<<<<< Updated upstream
- [x] 세부1(이수연) — `prod_ai` 1단계 폴백(NOS 필터 반영) 포함 커버리지 측정 — 고유명 30.46%, 신고건 **96.73%**, 미해결 445,806개(1장 참고)
- [x] 세부2(장수연) — 2단계 유사도 매칭(`similarity.py`) 구현 (PR#43 머지 완료)
- [x] 세부3(함하경) — 3단계 LLM 판정 + 전수 확인 (PR#17 머지 완료, 부작용 사전)
- [x] 세부3(함하경) — 3단계 임베딩 검색 + Recall@K + Langfuse (PR#47 머지 완료)
- [x] ★ 3단계 전 게이팅(triage, 장수연) — OUT_OF_SCOPE/BRAND_DOSE/UNKNOWN 3분할, **Z(UNKNOWN) = 419,674** (PR#48 머지 완료)
- [x] ★ 브랜드+용량 재정규화(이지훈) — BRAND_DOSE 버킷 해결 (PR#50 머지 완료)
- [x] ★ 3단계 하네스 뼈대(이지훈) — 후보합치기·LLM판정·저장/재개 구조, 가짜 부품(`FixtureSource`/`MockJudge`)으로 배관 확인 완료 (PR#52 머지 완료)
- [ ] ★ 하네스에 실제 컴포넌트 연결 — `CandidateSource`에 #47 FAISS·#43 `match_one` 연결(이지훈, **다른 모든 3단계 작업의 선행 조건**, 1장 참고)
- [ ] ★ 프롬프트 비교 — `stage3_judge_v1.txt`(이지훈) 1개뿐. 함하경·장수연·이수연 각자 버전 작성 후 비교(하네스 연결 후)
- [ ] ★ 운영점(신고건수 컷오프) 확정(장수연, 1장 참고) — GPU 산정의 실제 분모 결정
- [ ] GPU 산정 — Z=419,674(또는 운영점 적용 후 더 작은 수)가 입력값. 함하경이 사용 방식·비용 조사 진행 중(1장 참고)
- [ ] v1 자동 확정 규칙 결정 — 하네스는 지금 LLM MATCH/MULTI여도 자동 확정 안 하고 "보류"로만 둠(성분은 채움). DiAna dev set 채점 후 확정 규칙 정하기로 함(PR#52 "같이 정해야 할 것")
=======
- [x] 세부1(이수연) — `prod_ai` 1단계 폴백(NOS 필터 반영) 포함 커버리지 측정 — 고유명 30.46%, 신고건 **96.73%**, 미해결 445,806개(최종치 — 1장 참고)
- [x] 세부2(장수연) — 유사도 매칭(`similarity.py`) 머지 완료 (PR#43)
- [x] Triage(이수연) — OUT_OF_SCOPE·BRAND_DOSE·UNKNOWN 3-bucket 게이팅 완료 (PR#48·#50·#57). 운영점: `n_reports ≥ 2` 147,718개 (UNKNOWN 신고 86% 커버)
- [x] 세부3(함하경) — 부작용 사전 LLM 판정 + 전수 확인 완료 (PR#17, 118/120)
- [x] 세부3(함하경) — Stage 3-A alias corpus·FAISS 구축 (PR#47, SapBERT K=10 운영 결정)
- [x] 세부3 하네스(이지훈) — PR#52·#60 머지 완료 (뼈대 + FAISS·fuzzy 연결 + DiAna dev 채점 + leakage 필터)
- [x] 세부3 품질 게이트(함하경) — PR #62: alias corpus 복합제 collapse + 평가 체계 보정(OUT_OF_SCOPE·동의어 매핑) + 하네스 동시성(ThreadPool). 리뷰 반영 완료, 머지 대기. **retrieval 상한 재측정 결과 SapBERT∪fuzzy K=10 = 64.5%** (초기 30.6%는 평가 설계 결함)
- [x] Langfuse 연결(함하경) — `langfuse_tracer.py` 머지. Trace는 GPU 돌릴 때부터 쌓임
- [ ] GPU 파일럿·본 실행 — 이번 주 (아래 GPU 블록)
- [ ] 조인율 품질테이블(장수연) — Stage 3 결과 나온 뒤 착수

### GPU 운영점·결정 (2026-10-22 회의)

- ✅ **운영점**: `n_reports ≥ 2` (147,718개, UNKNOWN 신고 86% 커버)
- ✅ **인스턴스 안**: A — `g6.xlarge` 온디맨드(개발·벤치마크) → `g5.xlarge` Spot(본 실행)
- ✅ **AWS Quota 승인 완료**: Running On-Demand G·VT 8 vCPU / Spot 8 vCPU
- ✅ **예산 상한**: 팀 전체 $50 (1인당 ~$12.5 ≈ 1.8만원)

### 평가 기준 (실험 **전에** 선언)

점수 보고 기준 정하면 안 됨. 선언하고 시작한다.

1. `auto_precision ≥ 99%` — 위험한 자동 오매핑 차단
2. 조건 만족 모델 중 `auto_coverage` 최대
3. 성능 차이가 통계적으로 불명확하면 저렴한 모델 선택
4. `invalid_json_rate < 0.5%`
5. 재시도 포함 실패율 < 1%
6. 동률이면 `cost per 1,000` 낮은 설정
7. dev 526개 점 추정치는 **Wilson CI + 오답 건수**를 함께 보고

### 이번 주 역할 분담

| 날짜 | 작업 | 담당 |
|---|---|---|
| 10/22 (오늘) | PR #62 리뷰 반영·머지 | 함하경 (완료, 머지 대기) |
| 10/22 | GPU benchmark 코드 PR 작성 — `silver/drug_dict/benchmark.py` (하단 스펙) | 함하경 |
| 10/22~23 | 시스템 프롬프트 각자 작성 — `silver/drug_dict/prompts/stage3_judge_{이름}.txt` (하단 스펙) | **4인 각자** |
| 10/23 | GPU 인스턴스 환경 세팅 — Deep Learning AMI, vLLM, S3 role, Budget 알림 + 자동 종료 | 이지훈 |
| 10/23 | 파일럿 Phase 1 — dev 526개로 fuzzy+SapBERT baseline / Qwen 3B·7B·14B / 프롬프트 4종 / K=10·20 비교 → 열등 조합 제거 | 함하경 |
| 10/24 | 파일럿 Phase 2 — 남은 1~2개 조합으로 실데이터 1,000건 + concurrency 8/16/32/64 sweep + 5,000건 계층 표본 | 함하경 |
| 10/25 | 모델·프롬프트·동시성·운영점 최종 고정 → sealed test 1회 → 결과 정리·오류 분해 | 함하경 |
| 10/25 | 본 실행 147,718개 모니터링 (Spot 중단 대응·재개) | 이지훈 + 함하경 |
| 10/25~26 | B단계 조인 영향 테스트 (Stage 3 결과 → ingredient_set 재검증) | 이수연 |
| 10/25~26 | D단계 채점 영향 테스트 (unmatched 축소 → Recall@K 재측정) | 장수연 |
| 10/26 | 결과 발표용 자료 — 아키텍처·비용·recall·cost per 1,000·오류 분해 | 함하경 |

### 프롬프트 작성 가이드 (10/22~23, 4인 각자)

하네스 설계 원칙("1명이 하네스, 3명이 프롬프트" — PR #52)에 따라 **각자 다른 시스템 프롬프트**를 만들어서 비교한다. 파일 이름(stem)이 그대로 `prompt_version` 메타데이터가 돼서 결과·Langfuse에 기록됨.

**저장 위치**: `silver/drug_dict/prompts/stage3_judge_{이름}.txt`
- 함하경 → `stage3_judge_hakyung.txt`
- 이지훈 → `stage3_judge_jihoon.txt`
- 이수연 → `stage3_judge_suyeon.txt`
- 장수연 → `stage3_judge_suyeon2.txt` (혹은 성 포함 구분자)

**출력 포맷은 공통**: `{decision: MATCH|MULTI|NONE, candidate_ids: [...]}` JSON. 자유 생성 금지(§5 하네스 계약).

**변주 지점** (각자 자기 가설대로):
- **역할 지시**: "You are a pharmacist" vs "FAERS data analyst" vs 중립
- **few-shot 예시**: 0개 vs 2개 vs 5개 (brand→성분 점프 사례 포함 여부)
- **NONE/MULTI 판단 기준**: 애매하면 보수적으로 NONE vs 적극적으로 MATCH
- **후보 rendering**: 번호만 vs 성분 설명 포함
- **CoT 유도**: "step by step" 명시 vs 생략

**실행 방법**:
```bash
python stage3_harness.py run ... --prompt silver/drug_dict/prompts/stage3_judge_hakyung.txt
```

**비교 범위**: 운영 후보 모델(Qwen 7B)에 대해 4개 프롬프트 전부 돌림 (dev 526개 × 4 = 2,100건). GPU 시간 약 1~2시간. Phase 1에서 모델 좁혀진 뒤 Phase 2 전에 결과 공유.

### GPU benchmark 코드 스펙 (함하경, 10/22)

**PR 제목(안)**: `feat(silver): Stage 3 GPU benchmark와 모델 선택 평가 추가`

포함 내용:
- 실행 명령 (`silver/drug_dict/benchmark.py`)
- 모델·프롬프트·동시성 설정 argparse
- GPU runtime 메타데이터 자동 수집 (`nvidia-smi`, `vllm --version`, instance type)
- 품질 지표 + 처리량 지표 통합 측정
- 시간당 단가 입력하면 `cost_per_1000` 자동 계산
- 결과 JSON + CSV 생성
- Langfuse `run_id` 연결

run 메타데이터 스키마:
```json
{
  "run_id": "qwen7b-p2-r-sapbert-v2-c32",
  "model": "Qwen2.5-7B-Instruct-AWQ",
  "model_revision": "...",
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

### 최종 결정문 (함하경, 10/25)

평가 기준 통과 모델 중 비용 최소를 선택하고 **왜 이걸 선택했는지** 결정문 작성. LLM이 반드시 이겨야 할 필요는 없음.

좋은 결론의 예시:
> "Qwen 14B는 7B 대비 judge accuracy가 0.8%p 높았지만 신뢰구간이 겹쳤고, 처리량은 42% 낮고 1,000건당 비용은 68% 높았다. 따라서 운영 모델은 7B AWQ로 선택했다."

또는:
> "fuzzy+SapBERT rank fusion과 LLM judge의 성능 차이가 작아, retrieval 증거가 일치하는 사례는 GPU 없이 확정하고 충돌 사례에만 Qwen을 호출하도록 변경했다. 이를 통해 LLM 호출을 61% 줄였다."
>>>>>>> Stashed changes

---

## 4. B단계 — 펼치고 집계 (1.5개월, 리드: 이수연)

**게이트**: 순위표 v1 완주

- [x] 리드 — 그레인 결정 4개 확정(role_cod·caseversion·부작용 단위·중복 처리, 1장 참고) + `fct_case_drug_reaction` dbt contract 구현(`dbt build` PASS=20)
- [ ] 리드 — GOLD 4칸 집계 + IC025·묶음 그레인으로 뺄셈 유도(가장자리 합 트릭) — 다음 작업
- [ ] 세부1 — 8,400만 행 펼치기 + 성분 조인 + salting — `ingredient_set` 준비됨(1장 참고), 착수 가능(담당 미정 — C단계 인원 합류 전까지 리드가 겸임)
- [ ] 세부2 — 시점 델타 누적 + 재집계 대조
- [ ] 세부3 — 기준 파일·CI 픽스처·구조 검사

---

## 5. D단계 — 순위와 평가 (1.5개월, 리드: 장수연)

**게이트**: Recall@K · Lift · 리드타임 확정

**정답지 품질** (채점의 기준 자체라 D단계 착수 전 처리 권장, 장수연 담당)
- [x] ★ 라벨 분류 감사 완료 (PR#28, 12장) — 200건 표본 대신 2,268행 전수 검토, 자동분류 대비 116행 정정(양성 1,212/음성 397/보류 659), 정확도 96.8%. `labelset/` 패키지로 승격, source_url 재현성 버그도 함께 수정(PR#20 대체)
- [ ] ★ 정답지 2019년 분기 포함 여부 확인 — 2018 Q1~Q3는 PR#28로 반영 완료(1,715→2,268쌍), 2019년치는 미확인

- [ ] 리드 — 랭킹 가중치 학습 + MLflow — C단계 게이트 확정 후 착수
- [ ] 세부1 — 게이트·필터 구현 (동의어 묶음 21개 포함)
- [x] 세부2(장수연) — 채점기 골격(완전일치매칭·Recall@K·Lift·검토대상축소율·스모크) (PR#7 머지 완료)
- [x] 세부2(장수연) — 정답지 1,715쌍 분할 + 2023년 컷오프 (PR#15 머지 완료)
- [ ] 세부2(장수연) — Stage 3 결과 반영한 Recall@K 재측정 — 이번 주, §3 GPU 블록
- [ ] 세부2(장수연) — 전체 백테스트 — Stage 3·B단계 완료 후
- [ ] 세부3 — dbt 마트·FastAPI·화면 2개
