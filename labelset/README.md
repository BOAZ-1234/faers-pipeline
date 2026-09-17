# labelset — 정답지 수집·라벨링

FDA 분기 신호보고서에서 **정답지(채점 기준)**를 만든다. 계약: [`contracts/labelset.md`](../contracts/labelset.md)

> 승격 이력: 원래 `_probe/`(실험용, gitignore)에 있던 것을 정식 위치로 옮김.
> 2026-09 정답지 전수 감사(장수연) 반영판.

## 구성

| 파일 | 역할 |
|---|---|
| `download_fda_signals.py` | FDA 분기 신호보고서 스크랩(web.archive 스냅샷 경유) → `out/fda_signals.csv` + `out/fda_signals_raw.parquet`. `info`(300자 하위호환)와 `info_full`(무절단) 동시 저장 |
| `classify_signals.py` | `info_full` 텍스트 → `label3` ∈ {양성, 음성, 보류} 자동 3분류 |
| `out/fda_signals.csv` | 정답지 마스터 (gitignore). 컬럼 `product,signal,label,label_basis,info_full,info_status,...` |

## 실행

```bash
python -m labelset.download_fda_signals   # 원문 수집 (out/ 재생성)
python -m labelset.classify_signals       # info_full 기준 자동 라벨 → label3
```

## 라벨의 세 층

1. **자동(`label3`)** — `classify_signals` 규칙 출력. 전수 감사 대비 정확도 96.7%.
2. **정정(`label`)** — 사람/LLM 전수 검토 결과(census). 다제품 블록 등 규칙이 틀린 곳을 바로잡은 **정답**. 채점엔 이걸 쓴다.
3. **근거(`label_basis`)** — 각 행 판정이 어떤 유형이었는지(`mixed_reviewed`, `pure_update`, `trunc_verified/unverified`, `oldformat_reviewed` 등).

## 원문(info_full)과 봇 차단

`info`는 스크랩 시 300자로 잘렸었다(구). 지금은 `info_full`이 무절단 원문이며
**2,268행 중 2,247행(99.1%) 확보**, 21행만 미확보(`info_status='truncated_only'`, 전부 순수 양성).

⚠ 라이브 fda.gov 신호 페이지는 **사라진 게 아니라 봇 차단(abuse-detection)** 된다 —
`requests`로는 `apology_objects/abuse-detection`로 404, **브라우저로는 정상**. 그래서:
- `download_fda_signals.py`(requests+web.archive): 아카이브 스냅샷엔 되나 라이브 fda.gov엔 막힘.
- 신규 분기·절단분 원문은 **브라우저(Claude in Chrome)로 표 추출**해야 한다.
`out/fda_signals_raw.parquet`(원문)도 보존 소스로 함께 쓴다.

감사 절차·발견은 [`docs/labelset-audit-protocol.md`](../docs/labelset-audit-protocol.md).
