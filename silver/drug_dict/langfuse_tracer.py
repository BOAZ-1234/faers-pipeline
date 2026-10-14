"""
Stage 3-B Langfuse trace 헬퍼 (캐스케이드 설계 §7)

LLM 판정 1건 = Trace 1개. 구조:
  Trace (raw_drug_name)
    └ Generation (llm-judge)

langfuse 3.x(OTel 기반) 전용. 4.x는 start_as_current_span이 제거돼
trace()가 깨지고, 2.x의 /api/public/ingestion은 Cloud v4에서 실시간 반영이
안 된다. 버전은 requirements.txt에 `langfuse>=3,<4`로 핀돼 있다.

사용법:
  tracer = LangfuseTracer()
  with tracer.trace(raw_name, meta) as t:
      t.log_generation(prompt, response, usage)
      t.log_output(decision, ingredient, confidence, gold)

환경변수 (.env):
  LANGFUSE_SECRET_KEY
  LANGFUSE_PUBLIC_KEY
  LANGFUSE_HOST 또는 LANGFUSE_BASE_URL  (기본 https://cloud.langfuse.com)
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ENV = Path(__file__).parent.parent.parent / ".env"
if _ENV.exists():
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# langfuse 3.x는 LANGFUSE_HOST만 인식 — .env가 BASE_URL로 적혀 있으면 복사
if "LANGFUSE_BASE_URL" in os.environ and "LANGFUSE_HOST" not in os.environ:
    os.environ["LANGFUSE_HOST"] = os.environ["LANGFUSE_BASE_URL"]


@dataclass
class TraceContext:
    _lf: Any      # Langfuse 인스턴스
    _span: Any    # 루트 span (= trace)
    _meta: dict

    def log_generation(
        self,
        prompt: str,
        response: str,
        usage: dict | None = None,   # {"input": N, "output": M}
        model: str = "",
    ) -> None:
        usage_details = None
        if usage:
            usage_details = {"input": usage.get("input", 0), "output": usage.get("output", 0)}
        with self._lf.start_as_current_observation(
            as_type="generation",
            name="llm-judge",
            model=model or self._meta.get("llm_model") or os.environ.get("STAGE3_LLM_MODEL", "unknown"),
            input=prompt,
            output=response,
            usage_details=usage_details,
        ):
            pass

    def log_output(
        self,
        decision: str,
        ingredient: str,
        confidence: float | None = None,
        gold: str | None = None,
    ) -> None:
        out: dict[str, Any] = {"decision": decision, "ingredient": ingredient}
        if confidence is not None:
            out["confidence"] = confidence
        if gold is not None:
            out["gold"] = gold
            out["correct"] = ingredient.upper() == gold.upper()
        self._span.update_trace(output=out)


class LangfuseTracer:
    def __init__(self) -> None:
        from langfuse import Langfuse
        self._lf = Langfuse()  # 키·host는 환경변수에서 자동 로드

    @contextmanager
    def trace(self, raw_name: str, meta: dict):
        """
        Args:
            raw_name: FAERS 원본 약물명 (Trace 이름)
            meta: §7 필수 필드 dict — make_meta()로 생성
        """
        with self._lf.start_as_current_span(name=raw_name, input={"raw_drug_name": raw_name}) as span:
            span.update_trace(name=raw_name, metadata=meta, input={"raw_drug_name": raw_name})
            try:
                yield TraceContext(_lf=self._lf, _span=span, _meta=meta)
            finally:
                self._lf.flush()

    def flush(self) -> None:
        self._lf.flush()


def make_meta(
    *,
    raw_name: str,
    normalized_name: str,
    routing_reason: str,
    fuzzy_max_score: float | None = None,
    embedding_top_k: int = 10,
    candidate_ids: list[str] | None = None,
    pipeline_version: str = "c-v1",
    dictionary_version: str = "d-2026Q3",
    retrieval_version: str = "r-sapbert-v1",
    prompt_version: str = "er-v1",
    llm_model: str = "",
) -> dict:
    """§7 필수 metadata dict 생성."""
    m: dict[str, Any] = {
        "raw_drug_name": raw_name,
        "normalized_drug_name": normalized_name,
        "routing_reason": routing_reason,
        "embedding_top_k": embedding_top_k,
        "pipeline_version": pipeline_version,
        "dictionary_version": dictionary_version,
        "retrieval_version": retrieval_version,
        "prompt_version": prompt_version,
        "llm_model": llm_model or os.environ.get("STAGE3_LLM_MODEL", ""),
    }
    if fuzzy_max_score is not None:
        m["fuzzy_max_score"] = fuzzy_max_score
    if candidate_ids is not None:
        m["candidate_ids"] = candidate_ids
        m["candidate_count"] = len(candidate_ids)
    return m


ROUTING = {
    "NO_MATCH": "NO_MATCH",
    "FUZZY_LOW_MARGIN": "FUZZY_LOW_MARGIN",
    "FUZZY_CONFLICT": "FUZZY_CONFLICT",
}


if __name__ == "__main__":
    meta = make_meta(
        raw_name="KLACID",
        normalized_name="KLACID",
        routing_reason=ROUTING["NO_MATCH"],
        fuzzy_max_score=0.54,
        embedding_top_k=10,
        candidate_ids=["CLARITHROMYCIN", "AZITHROMYCIN"],
    )
    required = {"raw_drug_name", "normalized_drug_name", "routing_reason",
                "pipeline_version", "dictionary_version", "retrieval_version",
                "prompt_version", "embedding_top_k"}
    assert required.issubset(meta.keys()), f"누락 필드: {required - meta.keys()}"
    assert meta["candidate_count"] == 2
    print("make_meta 체크 통과")

    if os.environ.get("LANGFUSE_SECRET_KEY"):
        tracer = LangfuseTracer()
        with tracer.trace("KLACID-test", meta) as t:
            t.log_generation(
                prompt="후보: CLARITHROMYCIN, AZITHROMYCIN\n약물명: KLACID",
                response="CLARITHROMYCIN",
                usage={"input": 30, "output": 5},
            )
            t.log_output("MATCH", "CLARITHROMYCIN", confidence=0.95, gold="CLARITHROMYCIN")
        tracer.flush()
        print("Langfuse trace 전송 완료 — cloud.langfuse.com에서 확인 (수 분 지연 가능)")
    else:
        print("LANGFUSE_SECRET_KEY 없음 — 연결 테스트 스킵")
