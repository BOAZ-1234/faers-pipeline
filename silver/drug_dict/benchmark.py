"""
GPU 파일럿 벤치마크 — 모델 × 프롬프트 × concurrency 조합을 순차 실행하고
속도·비용·정확도를 한 표로 출력한다.

설계 원칙 (GPU 사용 방식·예상 비용 설계 §8·§9·§10):
  - 베이스라인(LLM 없음): fuzzy+FAISS Top-1 먼저, 이후 LLM 조합과 비교
  - 관측성 지표: TPOT, request/s, latency p50/p95
  - 정확성 지표: auto_precision, cand_recall (harness evaluate() 그대로)
  - run metadata: vllm_version, cuda_version, gpu_name, instance_type, model_revision,
                  max_model_len, concurrency, price_snapshot
  - 비용 재계산: entity 수 / rps / 3600 × 시간당 가격

실행 예시 (Phase 1 — dev 526개):
  python3 benchmark.py \\
    --input unmatched_for_stage3.csv \\
    --gold golden/output/diana_golden_dev.csv \\
    --base-url http://localhost:8000/v1 \\
    --models Qwen/Qwen2.5-3B-Instruct,Qwen/Qwen2.5-7B-Instruct-AWQ,Qwen/Qwen2.5-14B-Instruct-AWQ \\
    --prompts prompts/stage3_judge_v1.txt,prompts/stage3_judge_hakyung.txt \\
    --concurrency 1,8,16,32,64 \\
    --out-dir /tmp/bench \\
    --price-per-hour 0.9896

실행 예시 (baseline만):
  python3 benchmark.py --input unmatched_for_stage3.csv --gold golden/output/diana_golden_dev.csv \\
    --baseline-only --out-dir /tmp/bench
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import stage3_harness as h
from eval_recall import UNMATCHED_DEFAULT


# ── run metadata ──────────────────────────────────────────────────────────────

def collect_env_meta(base_url: str, model: str, price_per_hour: float) -> dict:
    """GPU 환경 메타 — 재현성에 필요한 버전·하드웨어 정보."""
    meta: dict[str, Any] = {
        "instance_type": os.environ.get("EC2_INSTANCE_TYPE", "unknown"),
        "price_per_hour_usd": price_per_hour,
    }

    # vLLM /version 엔드포인트
    try:
        import urllib.request
        with urllib.request.urlopen(base_url.rstrip("/").removesuffix("/v1") + "/version", timeout=5) as r:
            meta["vllm_version"] = json.loads(r.read()).get("version", "unknown")
    except Exception:
        meta["vllm_version"] = "unknown"

    # GPU 정보 (nvidia-smi)
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            timeout=5, text=True).strip().splitlines()[0]
        gpu_name, vram = [x.strip() for x in out.split(",")]
        meta["gpu_name"] = gpu_name
        meta["gpu_vram_mb"] = int(vram)
    except Exception:
        meta["gpu_name"] = "unknown"

    # CUDA 버전
    try:
        out = subprocess.check_output(["nvidia-smi", "--query-gpu=driver_version",
                                       "--format=csv,noheader"], timeout=5, text=True).strip()
        meta["cuda_driver_version"] = out.splitlines()[0].strip()
    except Exception:
        meta["cuda_driver_version"] = "unknown"

    # 모델 revision (HF commit SHA) — 로컬 캐시에 refs 파일이 있으면 읽고, 없으면 unknown
    meta["model_revision"] = _hf_revision(model)

    return meta


def _hf_revision(model_id: str) -> str:
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    safe_id = model_id.replace("/", "--")
    refs = hf_home / "hub" / f"models--{safe_id}" / "refs" / "main"
    if refs.exists():
        return refs.read_text().strip()
    return "unknown"


def _max_model_len_from_vllm(base_url: str) -> int | None:
    """vLLM /v1/models 에서 max_model_len을 읽는다."""
    try:
        import urllib.request
        with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=5) as r:
            data = json.loads(r.read())
        return data["data"][0].get("max_model_len")
    except Exception:
        return None


# ── latency 수집용 Judge 래퍼 ─────────────────────────────────────────────────

@dataclass
class _LatencySample:
    latency_s: float
    input_tokens: int
    output_tokens: int


class TimedJudge:
    """OpenAICompatJudge를 감싸서 각 호출의 latency·토큰 수를 기록한다."""

    def __init__(self, inner: h.OpenAICompatJudge):
        self._inner = inner
        self.samples: list[_LatencySample] = []
        self.model = inner.model
        self.prompt_version = inner.prompt_version

    def judge(self, entity: h.Entity, cands: list[h.Candidate]) -> h.Decision:
        t0 = time.perf_counter()
        d = self._inner.judge(entity, cands)
        elapsed = time.perf_counter() - t0
        self.samples.append(_LatencySample(
            latency_s=elapsed,
            input_tokens=d.usage.get("input", 0),
            output_tokens=d.usage.get("output", 0),
        ))
        return d

    def latency_stats(self, wall_clock_s: float) -> dict:
        """wall_clock_s: run_one()이 측정한 실제 경과 시간.
        concurrency > 1이면 요청들이 겹쳐 실행되므로 latency 합산이 아닌
        wall-clock 기준으로 rps를 계산해야 정확하다."""
        if not self.samples:
            return {}
        lats = [s.latency_s for s in self.samples]
        out_toks = [s.output_tokens for s in self.samples]
        total_out = sum(out_toks)
        # TPOT: 출력 토큰 1개당 평균 시간 (decode 병목 측정 지표) — 순차 latency 기반
        total_latency = sum(lats)
        tpot_ms = (total_latency / total_out * 1000) if total_out else None
        lats_sorted = sorted(lats)
        n = len(lats_sorted)
        return {
            "n_calls": n,
            "rps": round(n / wall_clock_s, 3) if wall_clock_s else None,
            "latency_p50_s": round(lats_sorted[int(n * 0.50)], 3),
            "latency_p95_s": round(lats_sorted[min(int(n * 0.95), n - 1)], 3),
            "tpot_ms": round(tpot_ms, 2) if tpot_ms else None,
            "input_tokens_mean": round(statistics.mean(s.input_tokens for s in self.samples), 1),
            "output_tokens_mean": round(statistics.mean(s.output_tokens for s in self.samples), 1),
        }


# ── 비용 추정 ─────────────────────────────────────────────────────────────────

def estimate_cost(n_dev: int, rps: float | None, price_per_hour: float,
                  n_op: int = 132_450,
                  retry_factor: float = 1.2) -> dict:
    if not rps:
        return {}
    return {
        # 실제 돌린 dev 규모 기준 extrapolation (--limit 여부와 무관하게 비교용)
        "est_gpu_hours_for_dev": round(n_dev / rps / 3600, 2),
        "est_cost_usd_for_dev": round(n_dev / rps / 3600 * price_per_hour * retry_factor, 2),
        # 운영 규모(n_reports≥2 기준, 132,450개) 기준 추정 — 벤치마크의 주 목적
        "est_gpu_hours_for_op": round(n_op / rps / 3600, 2),
        "est_cost_usd_for_op": round(n_op / rps / 3600 * price_per_hour * retry_factor, 2),
        "cost_per_1k_entities_usd": round(n_dev / rps / 3600 * price_per_hour * retry_factor / n_dev * 1000, 4) if n_dev else None,
    }


# ── 단일 run ──────────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    label: str               # "model/prompt@concurrency" 형식
    model: str
    prompt_version: str
    concurrency: int
    is_baseline: bool = False
    env_meta: dict = field(default_factory=dict)
    run_stats: dict = field(default_factory=dict)   # harness run() 반환값
    eval_result: dict = field(default_factory=dict)  # harness evaluate() 반환값
    latency: dict = field(default_factory=dict)
    cost_est: dict = field(default_factory=dict)
    elapsed_total_s: float = 0.0
    error: str = ""


def run_one(
    entities: list[h.Entity],
    sources: list,
    judge,
    gold: dict[str, str],
    out_dir: Path,
    label: str,
    price_per_hour: float,
    chunk_size: int,
    top_k: int = 10,
    is_baseline: bool = False,
    n_op: int = 132_450,  # 운영 규모 고정값 (est_*_for_op 계산용)
) -> RunResult:
    storage = h.LocalStorage(str(out_dir / label.replace("/", "_")))
    result = RunResult(label=label, model=getattr(judge, "model", "baseline"),
                       prompt_version=getattr(judge, "prompt_version", "none"),
                       concurrency=getattr(judge, "_concurrency", 1),
                       is_baseline=is_baseline)

    t0 = time.perf_counter()
    try:
        result.run_stats = h.run(
            entities, sources, judge, storage,
            top_k=top_k,
            chunk_size=chunk_size,
            concurrency=result.concurrency,
        )
        t_run_end = time.perf_counter()          # evaluate() 시간 제외
        result.eval_result = h.evaluate(storage, gold)
    except Exception as e:
        result.error = str(e)
        return result
    result.elapsed_total_s = round(time.perf_counter() - t0, 1)

    if isinstance(judge, TimedJudge):
        run_elapsed_s = t_run_end - t0           # LLM 호출 구간만
        result.latency = judge.latency_stats(run_elapsed_s)
        rps = result.latency.get("rps")
        result.cost_est = estimate_cost(len(entities), rps, price_per_hour,
                                        n_op=n_op)

    return result


# ── baseline (LLM 없이 Top-1) ─────────────────────────────────────────────────

class Top1Judge:
    """LLM 없는 baseline: 후보 중 1순위를 그대로 선택 (semantic_score 최고).
    비교 기준선 — '이 이상은 LLM이 줘야 의미 있다'의 하한."""
    model = "no-llm-top1"
    prompt_version = "no-llm-top1"

    def judge(self, entity: h.Entity, cands: list[h.Candidate]) -> h.Decision:
        if not cands:
            return h.Decision("NONE", note="no candidates")
        return h.Decision("MATCH", [1], "baseline: top-1 candidate")


# ── 결과 출력 ──────────────────────────────────────────────────────────────────

def _fmt_row(r: RunResult) -> dict:
    ev = r.eval_result
    lat = r.latency
    cost = r.cost_est
    return {
        "label": r.label,
        "is_baseline": r.is_baseline,
        "auto_precision": ev.get("auto_precision"),
        "accuracy": ev.get("accuracy"),
        "cand_recall": ev.get("cand_recall"),
        "none_rate": ev.get("none_rate"),
        "n_gold_entities": ev.get("n_gold_entities"),
        "rps": lat.get("rps"),
        "tpot_ms": lat.get("tpot_ms"),
        "latency_p50_s": lat.get("latency_p50_s"),
        "latency_p95_s": lat.get("latency_p95_s"),
        "output_tokens_mean": lat.get("output_tokens_mean"),
        "est_gpu_hours_for_147k": cost.get("est_gpu_hours_for_147k"),
        "est_cost_usd_for_147k": cost.get("est_cost_usd_for_147k"),
        "cost_per_1k_usd": cost.get("cost_per_1k_entities_usd"),
        "elapsed_s": r.elapsed_total_s,
        "error": r.error,
    }


def print_summary(results: list[RunResult]) -> None:
    rows = [_fmt_row(r) for r in results]
    col_w = {k: max(len(str(k)), max(len(str(row.get(k, ""))) for row in rows)) for k in rows[0]}
    header = "  ".join(str(k).ljust(col_w[k]) for k in col_w)
    print("\n" + "=" * len(header))
    print("BENCHMARK SUMMARY")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for row in rows:
        print("  ".join(str(row.get(k, "")).ljust(col_w[k]) for k in col_w))
    print("=" * len(header) + "\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GPU 파일럿 벤치마크 (§8·§9)")
    p.add_argument("--input", type=Path, default=HERE / "unmatched_for_stage3.csv",
                   help="triage UNKNOWN CSV")
    p.add_argument("--gold", type=Path,
                   default=HERE / "golden" / "output" / "diana_golden_dev.csv",
                   help="DiAna dev CSV")
    p.add_argument("--unmatched", type=Path, default=UNMATCHED_DEFAULT,
                   help="leakage 방지용 3단계 실제 입력(eval_recall과 동일)")
    p.add_argument("--faiss-index", type=Path, default=HERE / "output" / "faiss_sapbert.index")
    p.add_argument("--faiss-corpus", type=Path, default=HERE / "output" / "alias_corpus_sapbert.jsonl")
    p.add_argument("--map", type=Path, default=HERE / "drug_ingredient_map.csv")
    p.add_argument("--out-dir", type=Path, default=Path("/tmp/bench"),
                   help="run별 part 파일 저장 루트")
    p.add_argument("--results-json", type=Path, help="결과 JSON 저장 경로(생략 시 stdout만)")

    p.add_argument("--base-url", default=os.environ.get("STAGE3_LLM_BASE_URL", "http://localhost:8000/v1"))
    p.add_argument("--models", default="",
                   help="쉼표로 구분된 모델 ID (예: Qwen/Qwen2.5-7B-Instruct-AWQ)")
    p.add_argument("--prompts", default="",
                   help="쉼표로 구분된 프롬프트 파일 경로")
    p.add_argument("--concurrency", default="1",
                   help="쉼표로 구분된 concurrency 값 (예: 1,8,16,32,64). "
                        "vLLM GPU 포화용 — 부하 테스트 아님(배치 파이프라인)")

    p.add_argument("--baseline-only", action="store_true",
                   help="LLM 없는 Top-1 baseline만 실행")
    p.add_argument("--top-k", type=int, default=10, help="후보 수")
    p.add_argument("--chunk-size", type=int, default=200)
    p.add_argument("--limit", type=int, help="앞 N개 entity만(빠른 스모크 테스트)")
    p.add_argument("--min-reports", type=int, default=1)
    p.add_argument("--price-per-hour", type=float, default=0.9896,
                   help="GPU 시간당 비용 USD (기본: g6.xlarge 온디맨드 서울 리전)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    # 입력 로드
    entities = [e for e in h.group_entities(h.load_rows(args.input))
                if e.n_reports >= args.min_reports]
    gold, scope = h.load_stage3_gold(args.gold, args.unmatched)
    entities = [e for e in entities if h.gold_for_entity(e, gold)]
    if args.limit:
        entities = entities[:args.limit]
    print(f"평가 대상 entity: {len(entities):,}개 (leakage 방지 교집합, scope: {scope})")

    # 후보 소스 (공통)
    sources: list = []
    if args.faiss_index.exists() and args.faiss_corpus.exists():
        sources.append(h.FaissSource.from_files(args.faiss_index, args.faiss_corpus,
                                                 search_k=args.top_k))
    if args.map.exists():
        sources.append(h.FuzzySource(args.map))
    if not sources:
        print("경고: FAISS 인덱스와 사전 파일이 없어 NoSource로 실행합니다", file=sys.stderr)
        sources = [h.NoSource()]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_results: list[RunResult] = []

    # 1. LLM 없는 baseline (항상 실행)
    print("\n[baseline] LLM 없는 Top-1 실행 중...")
    bl = run_one(entities, sources, Top1Judge(), gold, args.out_dir,
                 label="baseline_top1", price_per_hour=args.price_per_hour,
                 chunk_size=args.chunk_size, top_k=args.top_k, is_baseline=True)
    all_results.append(bl)
    print(f"  auto_precision={bl.eval_result.get('auto_precision')} "
          f"cand_recall={bl.eval_result.get('cand_recall')} "
          f"({bl.elapsed_total_s}s)")

    if args.baseline_only:
        print_summary(all_results)
        if args.results_json:
            _save_json(all_results, args.results_json)
        return

    # 2. LLM 조합
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    prompt_paths = [Path(p.strip()) for p in args.prompts.split(",") if p.strip()]
    concurrencies = [int(c.strip()) for c in args.concurrency.split(",") if c.strip()]

    if not models:
        sys.exit("--models 필요 (예: Qwen/Qwen2.5-7B-Instruct-AWQ)")
    if not prompt_paths:
        prompt_paths = [h.DEFAULT_PROMPT]

    env_meta = collect_env_meta(args.base_url, models[0], args.price_per_hour)
    env_meta["max_model_len"] = _max_model_len_from_vllm(args.base_url)
    print(f"\n환경 메타(공통): {json.dumps(env_meta, ensure_ascii=False)}")

    for model in models:
        # 모델마다 revision이 다를 수 있어 env_meta를 각자 만들어 붙인다
        model_meta = {**env_meta, "model_revision": _hf_revision(model)}
        for prompt_path in prompt_paths:
            if not prompt_path.exists():
                print(f"경고: 프롬프트 파일 없음 — {prompt_path}", file=sys.stderr)
                continue
            for conc in concurrencies:
                label = f"{Path(model).name}/{prompt_path.stem}@c{conc}"
                print(f"\n[{label}] 실행 중...")
                inner = h.OpenAICompatJudge(args.base_url, model, prompt_path)
                judge = TimedJudge(inner)
                judge._concurrency = conc  # run_one이 읽는 값

                r = run_one(entities, sources, judge, gold, args.out_dir,
                            label=label, price_per_hour=args.price_per_hour,
                            chunk_size=args.chunk_size, top_k=args.top_k)
                r.env_meta = model_meta
                all_results.append(r)
                ev, lat = r.eval_result, r.latency
                print(f"  auto_precision={ev.get('auto_precision')} "
                      f"cand_recall={ev.get('cand_recall')} "
                      f"rps={lat.get('rps')} tpot_ms={lat.get('tpot_ms')} "
                      f"({r.elapsed_total_s}s)"
                      + (f" ERROR: {r.error}" if r.error else ""))

    print_summary(all_results)
    if args.results_json:
        _save_json(all_results, args.results_json)


def _save_json(results: list[RunResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in results:
        d = asdict(r)
        d["summary"] = _fmt_row(r)
        rows.append(d)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"결과 저장: {path}")


if __name__ == "__main__":
    main()
