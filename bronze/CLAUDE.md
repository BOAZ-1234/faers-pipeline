# Bronze 계층 작업 규칙

**bronze 폴더에서 작업을 시작하기 전에 이 파일을 항상 먼저 읽는다.**

## 0. 배경

bronze는 S3(Iceberg on `s3a://boaz-1234-825494477740-ap-northeast-2-an`)에 원본 데이터를 적재하는
계층이다. 이 버킷에 대한 조회(GET/LIST/read)는 건당 비용이 청구되므로,
확인 목적의 조회를 무심코 전체 스캔으로 날리면 그만큼 비용이 쌓인다.

## 1. 대원칙 — 조회는 기본 10건 제한

- 데이터가 잘 적재됐는지, 스키마가 맞는지 등을 "확인"하는 조회는
  **항상 10건(LIMIT 10) 정도만 뽑아서 테스트**한다.
- Spark/Iceberg 예시:
  ```python
  spark.sql(f"SELECT * FROM {table_name} LIMIT 10").show()
  df.limit(10).show()
  ```
- pandas/boto3로 S3 오브젝트를 직접 볼 때도 `MaxKeys=10` 등으로 개수를 제한하고,
  전체 prefix를 `list_objects_v2`로 다 훑지 않는다.

## 2. 10건을 넘는 조회가 필요하면 먼저 컨펌

다음과 같이 스캔 범위가 커지는 작업은 **실행 전 사용자에게 먼저 확인받는다**:

- `LIMIT` 없는 `SELECT *` / 전체 `count(*)` / 전체 테이블 `.collect()`, `.toPandas()`
- 파티션/버킷 prefix 전체를 순회하는 `list_objects_v2` 페이지네이션 전체 순회
- `describe history`, `describe extended` 등 전체 메타데이터 스캔
- 반복문 안에서 S3/Iceberg 조회를 여러 번 호출하는 배치성 검증 스크립트

컨펌 없이 위 작업을 바로 실행하지 않는다. 왜 더 큰 범위가 필요한지 이유를 먼저 말하고
승인을 받은 뒤에 실행한다.

## 3. 적재(쓰기) 작업은 이 규칙 대상이 아님

이 규칙은 "조회(read)"에 대한 비용 절감 규칙이다. `write_to_iceberg` 같은
정상적인 적재 파이프라인 실행 자체를 막는 규칙이 아니다.
