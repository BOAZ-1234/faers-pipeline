# 계약: 순위표 (ranking) — 채점기 입력

우리 파이프라인(FAERS 불균형 분석)이 내놓는 **후보 신호 순위표**. 채점기(`scoring/`)의 입력이다.

## 형식

정렬된 `(ingredient, reaction, score)` 행의 목록. **신호가 강한 순(내림차순)** 으로 정렬.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `ingredient` | str | 성분명 (정규화 완료). **이수연 `drug_ingredient_map` 통과값** |
| `reaction` | str | 부작용 PT (정규화 완료). **하경 `reaction_dict` 통과값** |
| `score` | float | 불균형 점수 등 랭킹 근거 (정렬에만 쓰이고 채점엔 미사용) |

Python 으로는 정렬된 `list[SignalKey]` 로 넘긴다:

```python
from scoring import make_key, score
ranking = [make_key(row.ingredient, row.reaction) for row in df_sorted_desc.itertuples()]
card = score(ranking, labelset.positives, universe_size=N)
```

## 키 규칙 (계획서 노트 ③ — 반드시 지킬 것)

- `ingredient`, `reaction` 은 **정답지와 똑같은 정규화**를 거친 값이어야 한다.
- 성분/부작용의 **의미 정규화**(브랜드→성분, 영국식 철자, 어순)는 상류 사전에서 끝낸다.
- 채점기는 마지막으로 `keys.canonical()`(공백·대소문자·앞뒤구두점만) 하나만 걸어 완전일치 판정한다.
- 따라서 순위표와 정답지는 **동일한 `ingredient_norm` / `reaction_pt` 표기 규칙**을 공유해야 한다. 이 규칙이 어긋나면 매칭이 0이 된다 → 1주차 조인키 회의에서 셋이 확정.

## 정렬·중복

- 채점기는 **입력 순서를 순위로 신뢰**한다. 반드시 score 내림차순으로 정렬해 넘길 것.
- 중복 키는 채점기가 **첫 등장만 남기고 제거**한다(상위 우선).

## 랜덤 기준선 N

- `universe_size` = 후보 우주 전체 크기 N (Lift·축소율의 분모).
- 생략 시 순위표 고유 후보 수로 본다. 순위표가 전체 후보를 다 담지 않는다면 명시적으로 N을 넘길 것.
