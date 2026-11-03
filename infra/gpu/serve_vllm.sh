#!/usr/bin/env bash
# vLLM OpenAI 호환 서버를 이 서버 안(127.0.0.1:8000)에 띄운다. 하네스·benchmark.py는 기본으로 여기에 붙는다.
#   bash infra/gpu/serve_vllm.sh Qwen/Qwen2.5-7B-Instruct-AWQ
#   MAX_MODEL_LEN=8192 bash infra/gpu/serve_vllm.sh Qwen/Qwen2.5-14B-Instruct-AWQ
# 모델을 바꿀 때는 Ctrl+C로 끄고 다시 실행한다(L4 24GB에 한 번에 한 모델).
# 끄지 않고 세션을 닫으려면 tmux 안에서 실행:  tmux new -s vllm  →  실행  →  Ctrl+B, D
set -euo pipefail

MODEL="${1:?사용: serve_vllm.sh <HuggingFace 모델 ID> [추가 vllm 인자...]}"
shift
export HF_HOME="${HF_HOME:-/opt/hf-cache}"
# FlashInfer top-p/top-k 샘플러는 기동 중 커널을 JIT 컴파일하는데 이 AMI(Ubuntu 26.04)에는 nvcc/CUDA toolkit이 없어
# "Could not find nvcc"로 엔진이 죽는다(2026-11-02 실측). 하네스는 temperature=0(greedy)이라 이 샘플러를 쓰지 않는다.
export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"

# 외부 LLM API가 아니라 이 서버 안에서만 연다(보안 그룹 인바운드 0개, 127.0.0.1 바인딩).
# --max-model-len 4096: 2048이면 넘치는 요청이 있다. 실데이터 600개(n_reports≥2 상위) Qwen 토크나이저 실측 —
#   입력 p99 1,464(v1)·1,823(hakyung), 최대 2,691·3,050토큰(후보에 성분 수십 개짜리 동종요법 제품이 섞인 경우).
#   출력 max_tokens 200을 더하면 2048에서 0.2~0.7%가 거절되고, 하네스는 3회 실패 시 실행을 멈춘다.
#   vLLM은 PagedAttention이라 KV cache를 요청별 최대 길이로 미리 잡지 않는다 — 4096으로 둬도 메모리 손해 없음.
# --max-num-seqs 64: 동시 처리 상한(설계값, 파일럿 concurrency sweep 8/16/32/64의 최댓값)
# --enable-prefix-caching: 매 요청 반복되는 시스템 프롬프트(300~650토큰)의 KV를 재사용
exec /opt/faers-gpu-venv/bin/vllm serve "$MODEL" \
  --host 127.0.0.1 --port 8000 \
  --max-model-len "${MAX_MODEL_LEN:-4096}" \
  --gpu-memory-utilization "${GPU_MEM_UTIL:-0.90}" \
  --max-num-seqs "${MAX_NUM_SEQS:-64}" \
  --enable-prefix-caching \
  "$@"
