# bronze/verification — 적재 완결성 검증 · 백필 기록

bronze(S3 Iceberg) 적재가 원본 zip과 일치하는지 대조하고, 발견한 구멍을 메운 **1회성 스크립트 모음**이다.
정기 파이프라인이 아니다. 2026-09 작업 기록이자, 다음 분기 적재 뒤 같은 방법으로 재검증할 때의 도구.

> 이 폴더의 스크립트는 로컬 `bronze/data/`의 원본 zip을 읽는다(gitignore). S3는 읽기 위주이고
> 쓰기는 `backfill_2013_gaps.py`, `add_source_zip_column.py` 두 개뿐이다(이미 적용 완료, 재실행 금지).

## 결과 요약

**FAERS (2012Q4~2026Q2, zip 55개)** — 로컬 zip 재파싱 vs 라이브 테이블

| | 백필 전 라이브 | 백필 후 라이브 | zip 원본 |
|---|---|---|---|
| faers_demo | 20,349,959 | 20,535,517 | 20,536,224 |
| faers_drug | 83,979,879 | 84,587,800 | 84,596,271 |
| faers_reac | 64,997,904 | 65,883,392 | 66,260,415 |

- **2013Q3** 전체(DEMO/DRUG/REAC)가 적재 안 돼 있었다 → 185,569 / 607,921 / 584,833행 추가.
- **2013Q4 REAC**이 부분 유실 → 기존 390,035행 삭제 후 690,690행 재적재.
- 남은 차이는 결함이 아니다.
  - REAC ~37.7만: 원본 zip 안의 **완전 동일 줄 중복**을 `compact_tables.py`가 제거한 것 (2022Q4 실측 23,879건이 정확히 일치).
  - DRUG/DEMO 소량: 같은 `primaryid`가 여러 분기 zip에 재등장(사례 재제출) — 분기별 비교의 착시.
- `faers_demo/drug/reac`에 `source_zip` 컬럼 추가(기존 행은 NULL, 이후 적재분부터 채워짐).

**AERS (2004Q1~2012Q3, zip 35개)** — 문제 0건. DRUG/REAC는 zip별로 정확히 일치, DEMO는 distinct ISR 4,271,506이 일치,
`load_manifest` 105건이 로컬 재파싱 결과(정상/격리 수)와 전부 같다.

## PR#33 리뷰에서 나온 지적과 확인 (9/22)

**"2013Q4 REAC 삭제 범위가 위험하지 않냐"**는 지적이 나왔다 — 그때 `source_zip` 컬럼이 없어서 삭제 키를
`primaryid`로 썼는데, `primaryid`는 사례가 나중 분기에 재제출되면 다른 zip에도 등장할 수 있어서
"이 zip 것만 지운다"가 보장되지 않는 방식이었다.

실제로 겹치는 게 있는지 원본 zip을 직접 대조했다: 2013Q4 REAC의 232,247개 `primaryid` 중 **11개가
다른 분기(2020Q3 9건·2013Q1 1건·2014Q2 1건)에도 등장**했다. 그 11건의 반응 내용을 두 분기에서
직접 비교하니 **한 글자도 다르지 않은 완전한 재제출**이었다 — 그래서 지우고 다시 채워도 최종 내용은
똑같이 나왔다(라이브 재대조에서도 이 11건 전부 불일치 0). **결과는 안전했지만 방식 자체가 안전했던
건 아니다** — 내용이 달랐다면 조용히 데이터가 빠졌을 것이다.

지금은 `source_zip` 컬럼이 있으므로 `backfill_2013_gaps.py`의 삭제 조건을 `primaryid` 대신
`source_zip`으로 바꿨다(`pipeline_common.delete_partial_rows`와 같은 패턴). 스크립트는 이미
`load_manifest`에 complete로 기록돼 있어 재실행해도 건너뛴다 — 이 수정은 데이터를 다시 고친 게
아니라, 다음에 비슷한 백필을 할 때 참고할 코드를 안전한 형태로 남긴 것이다.

## 스크립트 (실행 순서)

| 파일 | 하는 일 | S3 |
|---|---|---|
| `check_faers_expected_counts.py` | 로컬 zip을 로더와 같은 `classify_line`으로 파싱해 file_type별 기대 행 수 | 안 씀 |
| `extract_primaryids_from_zips.py` | (zip, primaryid) 목록 추출 → `primaryids/` | 안 씀 |
| `find_missing_quarters.py` | 위 목록 vs 라이브 primaryid anti-join → 분기별 누락 | 읽기 |
| `extract_row_counts_from_zips.py` | primaryid별 행 수 추출 → `row_counts/` | 안 씀 |
| `find_row_count_mismatches.py` | primaryid별 행 수 대조 (부분 유실 탐지) | 읽기 |
| `backfill_2013_gaps.py` | 2013Q3 추가 적재 + 2013Q4 REAC 삭제 후 재적재, `load_manifest` 기록 | **쓰기(적용 완료)** |
| `add_source_zip_column.py` | `source_zip` 컬럼 추가(메타데이터만) + 파티션 등록 | **쓰기(적용 완료)** |
| `verify_aers_independent.py` | AERS 독립 검증 (zip vs 라이브 vs `load_manifest`) | 읽기 |

산출물(`primaryids/`, `row_counts/`, `*.csv`, `*.parquet`, `*.json`)은 용량 때문에 커밋하지 않는다.

## 주의 — `load_faers_master.py` 재실행

`load_manifest`에 `pipeline='faers'` 기록은 백필한 2013Q3(3건)·2013Q4 REAC(1건)뿐이다. 나머지 분기는 기록이 없어서
**전체 zip 폴더를 대상으로 재실행하면 DRUG/REAC가 통째로 중복 append된다.** 신규 분기 zip만 `data/faers_not_load/`에 두고 돌릴 것.
(근본 해결: 기존 분기의 manifest 기록을 소급해서 채우기 — 이 폴더의 zip 재파싱 결과로 만들 수 있다.)

## 비용

bronze/CLAUDE.md 조회 규칙 준수: 읽기는 컴팩션된 소용량 테이블(faers 합계 ~2GB, aers ~0.26GB)에 대한 컬럼 단위 집계뿐이라
S3 요청·전송 비용은 사실상 0(AWS 월 100GB 무료 전송 한도 이내).
