# contracts/dbt — 조인키 계약 강제 (실행본)

C→B·D 경계의 조인키 *계약*을 검사한다.

기획서 §5-1: 조인키가 형식만 어긋나도 하류 조인(B 집계·D 채점·국내조인)이 **소리 없이**
깨진다 — 특히 "정답지 대조는 키가 틀리면 채점 자체가 불가능". 그래서 그 형식을
**테이블 만드는 시점(`dbt build`)에 검사**해 어긋나면 빌드를 실패시킨다.

## C단계 사전 두 개 → 어디서 쓰나

C단계 산출물인 사전 두 개의 역할 정리
B, D단계에서 사용되는 조인키는 다음과 같음

| 사전 | 조인키 | 무엇을 정규화하나 | 쓰는 단계 |
|---|---|---|---|
| **성분 사전** | `ingredient_set` · `unii` | FAERS **약물명**(자유텍스트) → 성분 | B(집계 조인) · D |
| **부작용 사전** | `pt_set_id` | **정답지의 '사람이 쓴 부작용문'** → 표준 용어 묶음 | D(채점 대조) |


## 부작용 키 통일 (계획서 기준)

상류 `silver/reaction_dict/` 실제 출력 컬럼이 계획서 키와 달라서, 
이 계약의 staging 모델에서 **계획서 키로 통일(alias)** 한다.

| 상류 reaction_dict 컬럼 | 계획서 키 | 위치 |
|---|---|---|
| `signal` | `signal_term` (§5) | `stg_reaction_dict.sql` alias |
| `pt_set` | `pt_set_id` (§5-1) | `stg_reaction_dict.sql` alias |

> `pt_set_id` 형식 = **소문자 · 알파벳 정렬 · `\|` 연결 · 하나짜리도 묶음**.
> 성분셋과 달리 **내부 공백은 허용**한다 — PT 용어가 원래 공백을 갖는다(`pancreatitis acute`).

-> 상류 출력 컬럼도 계획서 기준으로 통일하면 좋음

## 구성

```
contracts/dbt/
├─ dbt_project.yml / profiles.yml   # 로컬 duckdb
├─ seeds/
│  ├─ resolution_sample.csv         # [성분] 최종 확정: 약이름당 1행
│  ├─ candidate_sample.csv          # [성분] 후보 이력: 약이름당 여러 행
│  └─ reaction_dict_sample.csv      # [부작용] signal→pt_set: signal당 1행
├─ models/staging/
│  ├─ stg_resolution.sql            # [성분] contract enforced
│  ├─ stg_candidate.sql             # [성분] contract enforced
│  ├─ stg_reaction_dict.sql         # [부작용] contract enforced (signal_term/pt_set_id 통일)
│  └─ schema.yml                    # ★ 계약: 컬럼 타입·제약·테스트
└─ tests/                           # 형식 계약(단일 테스트)
   ├─ assert_resolved_ingredient_set_format.sql   # [성분] 소문자·공백없음
   ├─ assert_ingredient_set_sorted.sql            # [성분] 알파벳 정렬
   ├─ assert_unii_length.sql                       # [성분] 10자 또는 빈값
   ├─ assert_resolved_has_ingredient.sql          # [성분] 확정/수기면 성분 필수
   ├─ assert_pt_set_id_format.sql                 # [부작용] 소문자(공백 허용)
   └─ assert_pt_set_id_sorted.sql                 # [부작용] 알파벳 정렬
```

> `seeds/*.csv`는 지금 **샘플 픽스처**임. 실제로는 C단계 산출을 dbt `source`로 연결하면 됨

## 테이블 셋 — 성분이 왜 2개(Resolution + Candidate)인가

**B·D가 실제로 조인하는 건 Resolution뿐**이다(약이름당 1행 = 최종답, `ingredient_set`·`unii` 원천).
조인키만 보면 Resolution만 있으면 된다. Candidate는 **조인 대상이 아니라 감사·추적용**이다.

| 테이블 | 그레인 | 용도 | 조인키 원천? |
|---|---|---|---|
| `stg_resolution` | 약이름당 **1행** (최종 확정) | **B·D가 조인** | ✅ 예 |
| `stg_candidate` | 약이름당 **여러 행** (후보 이력) | 감사·디버깅("왜 이 성분으로 확정됐나") | ❌ 아니오 |

Candidate는 캐스케이드 각 단계(1=사전 / 2=fuzzy / 3=embedding)가 낸 후보와 점수
(`lexical_score`·`semantic_score`)를 남기는 **추적 로그**다. 확정이 이상해 보일 때
"어떤 후보가 있었고 왜 얘가 이겼나"를 되짚는 용도.

> **그런데도 계약에 넣은 이유는 딱 하나 — 다리 무결성(아래 `relationships`).**
> "모든 후보가 실재하는 확정본을 가리키는가"를 보장하려면 Candidate도 계약 안에 있어야 한다.
> 이 검사가 필요 없다면 Candidate는 빼도 된다. 비용이 거의 0이고 감사 추적이 C단계의 핵심이라 남긴다.

## 실행

```bash
pip install dbt-duckdb

# Windows는 한글 파일 때문에 UTF-8 모드 필요
PYTHONUTF8=1 dbt build --project-dir contracts/dbt --profiles-dir contracts/dbt
```

`dbt build` = seed 적재 → 계약모델 생성(타입·제약 강제) → 테스트 실행. 전부 통과하면 `PASS=14`.
지금은 로컬에서 이 명령으로 검증한다(CI는 아래 '다음 단계' 참고).

## 계약이 실제로 막는지 확인 (깨보기)

seed에서 한 값을 일부러 어긋내고 다시 `dbt build`:

| 사전 | 깨는 방법 | 잡는 검사 |
|---|---|---|
| 성분 | `H1250JIK0A` → `H1250JIK0` (UNII 9자) | `assert_unii_length` FAIL |
| 성분 | `carbidopa\|levodopa` → `Carbidopa\|Levodopa` (대문자) | `assert_resolved_ingredient_set_format` FAIL |
| 성분 | `carbidopa\|levodopa` → `levodopa\|carbidopa` (순서) | `assert_ingredient_set_sorted` FAIL |
| 성분 | `확정` 행의 성분셋을 비움 | `assert_resolved_has_ingredient` FAIL |
| 성분 | `resolution_status`에 `대기` 같은 값 | `accepted_values` FAIL |
| 부작용 | `dress\|sjs\|ten` → `DRESS\|sjs\|ten` (대문자) | `assert_pt_set_id_format` FAIL |
| 부작용 | `dress\|sjs\|ten` → `ten\|sjs\|dress` (순서) | `assert_pt_set_id_sorted` FAIL |
| 부작용 | `signal` 값을 중복시킴 | `unique` 제약 FAIL |

→ `dbt build`가 `ERROR`로 멈추고 테이블을 내보내지 않는다. (확인 후 값 복구)

## 계약 요약 (schema.yml)

**성분 (stg_resolution / stg_candidate)**
- 타입·제약: `entity_id` not_null+unique(약이름당 1행), `resolution_status` not_null + 4개 값만(accepted_values).
- 형식(tests/): 성분셋 소문자·공백없음·정렬, UNII 10자, 확정이면 성분 필수.
- 다리 무결성: 모든 Candidate.`entity_id`는 Resolution에 존재(relationships).

**부작용 (stg_reaction_dict)**
- 타입·제약: `signal_term` not_null+unique(표기당 1행), `status` not_null.
- 형식(tests/): `pt_set_id` 소문자·정렬(공백 허용). 미매칭 signal은 `pt_set_id` 빈값 허용.

### accepted_values · relationships 란 (dbt 기본 테스트)

위 6개 형식 검사는 우리가 `tests/`에 SQL로 짠 것이고, 아래 둘은 **dbt가 기본 제공하는 제네릭
테스트**를 `schema.yml`에서 한 줄로 부른 것이다. (그래서 데이터 테스트 8 = 형식 6 + 이 2개.)

- **`accepted_values`** — "이 컬럼엔 정해진 값만" 검사. `resolution_status`가 `확정/보류/미해결/수기`
  4개 중 하나여야 한다. `대기` 같은 값이 들어오면 FAIL. → 상태값을 못박는 용도.
- **`relationships`** — 참조 무결성(외래키 같은 것) = **다리 무결성**. 모든
  `stg_candidate.entity_id`가 `stg_resolution`에 **존재**해야 한다. 실재하지 않는 확정본을
  가리키는 고아 후보가 있으면 FAIL.
  ```
  Candidate (여러 행) ──entity_id──▶ Resolution (1행)
                       이 화살표가 안 끊겼나 = relationships
  ```

> 형식 규칙은 채점기가 마지막에 거는 `keys.canonical()`이 기대하는 표기와 1:1이어야 한다
> (노트 ③, [`../ranking.md`](../ranking.md)). 계약을 통과해도 표기 규칙이 어긋나면 매칭이 0이 된다.

## 다음 단계

- [ ] seed → 실제 C단계 산출 `source`로 교체 (성분: resolution/candidate · 부작용: reaction_dict)
- [ ] 상류 `silver/reaction_dict/` 컬럼명(`signal`/`pt_set`)을 계획서 키로 맞출지 함하경과 확정
      (지금은 이 계약의 staging에서 alias로 통일 중)
- [ ] 성분 사전의 `resolution_status` / 부작용 사전의 `status` 값 집합 확정(accepted_values) 성분 사전: 지금은 확정 / 보류/미해결/수기 4개인데 확정인지 (일단 schema.yml에 현재 기준으로 values 등록해놓음), 부작용 사전: 지금은 상태값 자체가 코드마다 다름 -> ok, None 등.. 이거 확정 필요
- [ ] **실데이터 `source` 연결 후 함께 추가** (지금은 seeds 픽스처라 둘 다 의미 없어 보류):
      - **조인율 품질테이블** — status로 집계해 조인율 적재, 기준선 미달 시 파이프라인 정지(§5-1 후반)
      - **CI** — 사전 (재)생성 시 실데이터에 `dbt build`를 돌려 형식 계약 검사(§6).
        (PR 코드 회귀만 볼 거면 픽스처로 도는 가벼운 워크플로를 따로 둘 수도 있음)
- [ ] (별개·나중) D단계 dbt 마트 — §5-2 국내조인, `serving/` 아래
