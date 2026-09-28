# bronze — 원본 적재 (A단계)

## 폴더 구조

| 폴더 | 용도 |
|---|---|
| [`loaders/`](loaders/) | **운영 파이프라인.** FAERS/AERS/openFDA를 S3 Iceberg에 적재하는 스크립트와 공유 모듈(`pipeline_common.py`, `spark_session.py`) |
| [`maintenance/`](maintenance/) | 적재 후 정리 작업. 컴팩션(`compact_tables.py`), 과거 버그로 생긴 쓰레기 데이터 정리(`cleanup_bad_aers_run.py`) — 둘 다 필요할 때만 수동 실행 |
| [`diagnostics/`](diagnostics/) | 읽기 전용 진단·진행상황 확인 스크립트 모음. 대부분 특정 시점(특정 분기, 특정 버그)을 조사하려고 만든 1회성 도구라 이름이 그 상황을 가리킨다 |
| [`handoff/`](handoff/) | bronze 데이터를 다음 단계(C단계) 입력으로 넘기는 스크립트. `stage_a_raw`를 읽고 `stage_b_silver`에 쓴다 |
| [`verification/`](verification/) | 적재 완결성 검증 + 발견한 구멍 백필 기록. 자체 README 있음 |
| `data/` | 원본 zip (gitignore, 로컬 전용) |
| `CLAUDE.md` | **작업 시작 전 항상 먼저 읽는다** — S3 조회 비용 규칙 |

## 실행 규칙

- 이 폴더의 스크립트는 **`bronze/`를 현재 디렉터리로 두고 실행**한다 (`data/faers_load/...`처럼 상대경로를 쓰기 때문). `verification/`만 예외로 `__file__` 기준 경로라 어디서 실행해도 된다.
  ```bash
  cd bronze && python loaders/load_faers_master.py
  ```
- `loaders/` 밖에서 `spark_session.py`나 `pipeline_common.py`를 쓰려면 경로를 먼저 잡는다:
  ```python
  import sys, os
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "loaders"))
  from spark_session import build_spark
  ```
  (`verification/`의 스크립트들이 이 패턴의 실제 예시다.)

## `diagnostics/`에 관해

대부분 특정 문제 하나를 확인하려고 그때그때 만든 스크립트다(예: `check_2013q4_progress.py`, `check_reac_completeness.py`). 적재 완결성을 전체적으로 확인하려면
이제 [`verification/`](verification/)이 더 정확하고 포괄적이다(원본 zip 전체 재파싱 대조) — 여기 있는 개별 스크립트들은 그 이전에 쌓인 기록으로 남겨둔다.
