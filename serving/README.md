# FAERS Signal Scores — 서빙 계층

`fct_signal_scores` 순위표(성분×PT별 신호 통계, 3.8M 행 / 163MB)를 저지연 REST API로 서빙한다.

## 왜 DuckDB + FastAPI (Trino 아님)

이 데이터 규모(163MB)에선 Trino가 과설계다. Trino는 데이터레이크 위 대화형/페더레이션 분석 엔진이라
쿼리 플래닝 오버헤드·파일 스캔·코디네이터/카탈로그 인프라가 저지연 API 백엔드엔 맞지 않는다.

DuckDB를 인메모리로 상주시키면:
- 인프라 0 (단일 프로세스, 코디네이터·카탈로그·워커 불필요)
- startup ~5s 적재 + ART 인덱스, 점 조회 **~20ms**
- S3의 파케이도 httpfs로 직접 read 가능

Trino는 "여러 소스 페더레이션 + TB급 애드혹 분석" 레이어로 분리하는 게 맞다.

## 실행

```bash
pip install -r serving/requirements.txt

# 로컬 파케이 (기본값: _probe/data/fct_signal_scores.parquet)
uvicorn serving.app:app --host 0.0.0.0 --port 8000

# S3 소스 (실제 업로드 위치) — DuckDB aws 확장의 credential_chain 사용
# 자격증명은 AWS 기본 체인(env → 프로파일 → EC2 인스턴스 Role)에서 자동 획득
FAERS_SIGNALS_SOURCE=s3://boaz-1234-825494477740-ap-northeast-2-an/serving/fct_signal_scores.parquet \
uvicorn serving.app:app --host 0.0.0.0 --port 8000
# 로컬에서 테스트할 땐 프로파일 지정: AWS_PROFILE=faers FAERS_SIGNALS_SOURCE=s3://... uvicorn ...
# EC2(faers-work)에서 띄우면 인스턴스 Role(faers-ec2-role)로 키 주입 없이 읽힘
```

리전 기본값은 `ap-northeast-2`이며 `FAERS_S3_REGION`으로 바꿀 수 있다.

대화형 문서: `http://localhost:8000/docs`

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `FAERS_SIGNALS_SOURCE` | `_probe/data/fct_signal_scores.parquet` | 로컬 경로 또는 `s3://...` |
| `FAERS_S3_REGION` | `us-east-1` | S3 소스일 때 리전 |

## 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | 헬스체크 |
| GET | `/stats` | 행수·성분수·PT수·소스 |
| GET | `/signals/{ingredient}` | 한 성분의 PT별 신호 (정렬·필터) |
| GET | `/pt/{pt}/top` | 한 PT에 대해 신호 강한 성분 상위 |
| GET | `/pair?ingredient=&pt=` | 성분×PT 단일 쌍 |
| GET | `/top` | 전체 상위 신호 (기본 `min_a=3`) |

공통 쿼리 파라미터: `order`(ic025·ic·ror·prr·a), `min_a`, `min_ic025`, `limit`(≤500), `offset`.

예:
```bash
curl "localhost:8000/signals/gadodiamide?limit=5"
curl "localhost:8000/pt/Keratopathy/top?min_a=3&limit=5"
curl "localhost:8000/pair?ingredient=gadodiamide&pt=Nephrogenic%20systemic%20fibrosis"
curl "localhost:8000/top?min_ic025=0&order=ic025&limit=20"   # MHRA 게이트(ic025>0) 상위
```

## S3 업로드 현황 (완료)

2026-11-19 적재 완료 (`--profile faers`):

```
s3://boaz-1234-825494477740-ap-northeast-2-an/
  serving/fct_signal_scores.parquet          # 서빙 순위표 (163MB, 3.8M행)
  serving/inputs/resolution_merged.csv       # dbt 재현용 입력
  serving/inputs/raw_name_entity_map.csv
  serving/inputs/candidate_merged.csv
```

재업로드:
```bash
aws s3 cp _probe/data/fct_signal_scores.parquet \
  s3://boaz-1234-825494477740-ap-northeast-2-an/serving/fct_signal_scores.parquet --profile faers
```
