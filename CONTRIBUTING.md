# 협업 규칙 (Contributing)

## 0. 대원칙

1. main 직접 push 금지. 머지는 PR로만, 코드리뷰 승인 1인 이상 + CI(lint + 단위테스트 + 기준 파일 비교) 통과 필수.
2. PR 하나 = 한 관심사. 리뷰 30분 넘는 크기는 쪼갠다.

---

## 1. 브랜치

- `main` 보호 브랜치, 직접 push 금지.
- 브랜치명: `<타입>/<단계>-<이슈번호>-<짧은-설명>`

| 요소 | 값 |
|---|---|
| 타입 | `feat` `fix` `refactor` `test` `docs` `chore` `data` |
| 레이어 | `bronze` `silver` `gold` `serving` `dags` `contracts` (레포 폴더명) |
| 이슈번호 | 연결된 이슈 # |
| 설명 | 소문자 + 하이픈, 3~5단어 |

```
feat/bronze-12-backfill
feat/silver-42-drug-ingredient-map-schema
fix/silver-58-salting-skew
data/silver-73-reaction-dict-316
```

(단계 A/C/B/D는 이슈 라벨 `stage:*`로 표시한다.)

- `data/`: 사전·픽스처·기준 파일 등 코드 아닌 산출 데이터 변경.
- Squash and merge. 머지 후 브랜치 삭제. main과 벌어지면 rebase.

---

## 2. 커밋 (Conventional Commits)

```
<타입>(<스코프>): <제목>
```

- 타입: `feat` `fix` `refactor` `test` `docs` `chore` `data`
- 스코프: `bronze` `silver` `gold` `serving` `dags` `contracts` (레포 폴더명)
- 제목: 한국어 OK, 명령형·현재형, 마침표 없음, 50자 이내

```
feat(silver): 약물명 정규화 1단계 사전 조회 추가
fix(silver): salting 키 분산에서 aspirin 쏠림 처리
data(silver): 부작용 사전 316개 전수 확인 반영
```

- 이슈 연결: 푸터에 `Refs #42` 또는 `Closes #42`.

---

## 3. 이슈

- 제목: `[단계] 동사로 시작하는 한 줄`
- 본문 필수: 계획서 참조 절(`4-2`, `5-1` 등) · 완료 조건(DoD).

### 라벨

| 종류 | 라벨 |
|---|---|
| 단계 | `stage:A` `stage:C` `stage:B` `stage:D` |
| 레이어 | `layer:bronze` `layer:silver` `layer:gold` `layer:serving` |
| 종류 | `type:feat` `type:bug` `type:task` `type:decision` `type:data-quality` |
| 우선순위 | `P0` `P1` `P2` |
| 상태 | `blocked` `needs-review` `good-first-task` |

---

## 4. PR

- 제목: 커밋 형식과 동일 (`feat(C): ...`).
- 본문: 이슈 링크(`Closes #42`) · 검증 내용.
- 머지 조건: CI 통과 + 리뷰 승인 1인 이상 + Conversation 전부 resolve.

---

## 5. 코드 리뷰

- 24시간 내 첫 반응.
- 리뷰어는 저자와 다른 사람.
- 지적은 근거와 함께. 취향은 `nit:` 접두어, 블로킹 금지.

---

## 6. 워크플로

```
이슈 → 브랜치 → 커밋 → PR(Closes #) → CI + 리뷰 → Squash merge → 브랜치 삭제
```

- 주간 동기화 1회.
