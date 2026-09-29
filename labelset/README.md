# labelset — 정답지 수집·라벨링

FDA 분기 신호보고서에서 **정답지(채점 기준)**를 만든다. 계약: [`contracts/labelset.md`](../contracts/labelset.md)

> 원래 `_probe/`(실험용, gitignore)에 있던 것을 정식 위치로 승격. 2026-09 정답지 전수 감사(장수연) 반영.

## 구성

| 파일 | 역할 |
|---|---|
| `download_fda_signals.py` | FDA 분기 신호보고서 스크랩 → `out/fda_signals.csv`. `info`(300자 하위호환)와 `info_full`(무절단) 저장 |
| `classify_signals.py` | `info_full` → `label3` ∈ {양성, 음성, 보류} 자동 3분류(규칙) |
| `out/fda_signals.csv` | 정답지 마스터 (gitignore) |

## 실행

```bash
python -m labelset.download_fda_signals   # 원문 수집 (out/ 재생성)
python -m labelset.classify_signals       # info_full 기준 자동 라벨 → label3
```

## fda_signals.csv 컬럼

| 컬럼 | 뜻 |
|---|---|
| `product`, `signal` | 정답지 쌍 |
| `label` | **최종 라벨(정답)** — 사람/LLM 전수 검토본. 채점엔 이걸 쓴다 |
| `label_basis` | 그 행 판정 근거(`mixed_reviewed`, `pure_update`, `oldformat_reviewed` 등) |
| `label3` | 자동 분류기 출력(참고). `label`과 다르면 분류기가 틀린 것 |
| `info_full` | 절단 안 된 전체 원문 |
| `info_status` | `already_full` / `recovered` / `truncated_only` |

## 라벨의 두 층 (중요)

1. **자동(`label3`)** — `classify_signals` 규칙 출력. 전수 검토 대비 정확도 96.8%.
2. **정답(`label`)** — 2,268행을 원문 대조해 사람/LLM이 검증·정정한 결과. 자동 대비 **116행 정정**
   (다제품 블록에서 일부만 조치인데 전부 양성 처리한 것 등). 최종 **양성 1,212 / 음성 397 / 보류 659**.

규칙 분류기는 다제품 블록 귀속에서 ~83%가 상한이라, 그런 블록은 `label`(검토본)이 정답이다.
분류기가 왜 그렇게 틀리는지, 어떤 문장이 근거인지 확인하려면 원문(`info_full`)을 대조하면 된다.

## 원문(info_full)과 봇 차단 — 꼭 인지

- `info`는 스크랩 시 300자로 잘렸었다. `info_full`이 무절단 원문이며 **2,267/2,268행(99.96%) 확보**,
  1행만 미확보(현재 아카이브에 해당 분기 버전 부재, 라벨은 양성 확정).
- ⚠ 라이브 fda.gov 신호 페이지는 **사라진 게 아니라 봇 차단(abuse-detection)** 이다 —
  `requests`로는 `apology_objects/abuse-detection`로 404, **브라우저로는 정상**. 따라서:
  - `download_fda_signals.py`(requests+web.archive): 아카이브 스냅샷엔 되나 라이브 fda.gov엔 막힘.
  - **신규 분기·절단분 원문은 브라우저(Claude in Chrome)로 표를 추출**해야 한다.

## 데이터 보존

`out/`·`*.csv`는 gitignore라 정답지(특히 `label` 컬럼)는 git에 안 들어간다. **팀 공유 저장소
(R2/드라이브)에 별도 보존**할 것 — 로컬 삭제로 유실하지 않도록. 코드(분류기·다운로더)만 추적된다.
