#!/usr/bin/env bash
# GPU 서버(faers-gpu, Deep Learning OSS Nvidia Driver AMI, Ubuntu) 최초 1회 세팅. 여러 번 실행해도 안전하다.
#   Session Manager 로 접속 → 본인 폴더에 레포 clone → 레포 루트에서:  bash infra/gpu/setup_gpu.sh
#
# 하는 일
#   1. GPU·드라이버 확인
#   2. uv 설치 → Python 3.12 + 공용 가상환경(/opt/faers-gpu-venv)
#      OS 기본 파이썬(Ubuntu 26.04)을 쓰지 않는다 — vLLM 휠이 지원하는 버전으로 고정해야 해서.
#      AMI에 들어 있는 PyTorch도 쓰지 않는다 — vLLM이 맞는 torch를 같이 설치한다.
#   3. vLLM + 하네스 의존성(silver/drug_dict/requirements.txt, rapidfuzz, boto3) 설치
#   4. 모델 캐시(/opt/hf-cache)·환경변수(/etc/profile.d/faers-gpu.sh)
#   5. GPU 유휴 자동 중지 타이머(faers-gpu-idle-stop.timer)
# 하지 않는 일: 레포 clone(사용자별 폴더에서 각자), 키 저장(서버는 인스턴스 역할 faers-ec2-role로 S3 접근)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV=/opt/faers-gpu-venv
PY_VER=3.12
UV_PY_DIR=/opt/uv-python
HF_CACHE=/opt/hf-cache
VLLM_VERSION="${VLLM_VERSION:-}"   # 비우면 최신. 재현이 필요하면 VLLM_VERSION=0.x.y bash setup_gpu.sh

echo "== 1. GPU 확인"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

echo "== 2. uv + Python ${PY_VER}"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi
sudo mkdir -p "$UV_PY_DIR"
sudo env UV_PYTHON_INSTALL_DIR="$UV_PY_DIR" uv python install "$PY_VER"
if [ ! -x "$VENV/bin/python" ]; then
  sudo env UV_PYTHON_INSTALL_DIR="$UV_PY_DIR" uv venv --python "$PY_VER" "$VENV"
fi

echo "== 3. vLLM + 하네스 의존성"
sudo env UV_PYTHON_INSTALL_DIR="$UV_PY_DIR" uv pip install --python "$VENV/bin/python" \
  "vllm${VLLM_VERSION:+==$VLLM_VERSION}" \
  -r "$REPO_ROOT/silver/drug_dict/requirements.txt" \
  rapidfuzz boto3 pandas pytest
sudo chmod -R a+rX "$VENV" "$UV_PY_DIR"

echo "== 4. 모델 캐시·환경변수"
sudo mkdir -p "$HF_CACHE"
sudo chmod 1777 "$HF_CACHE"   # 팀원 모두 같은 ssm-user지만, 다른 계정으로 들어와도 모델을 다시 받지 않게
sudo tee /etc/profile.d/faers-gpu.sh >/dev/null <<EOF
# faers-gpu 공용 환경 (infra/gpu/setup_gpu.sh가 생성)
export HF_HOME=$HF_CACHE
export STAGE3_LLM_BASE_URL=http://127.0.0.1:8000/v1
# benchmark.py가 결과 메타에 남긴다. 같은 이미지로 Spot(g5)을 띄워도 맞게 접속 때마다 조회
# -f: 실패 시 HTTP 오류 본문(HTML)이 값으로 들어가지 않게
_t=\$(curl -sf -m 2 -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60" || true)
export EC2_INSTANCE_TYPE=\$(curl -sf -m 2 -H "X-aws-ec2-metadata-token: \$_t" http://169.254.169.254/latest/meta-data/instance-type || echo unknown)
unset _t
EOF
# Session Manager 기본 셸은 sh(dash)라 /etc/profile.d를 읽지 않고 source도 없다. 접속 후 bash -l 로 들어가게 안내(README)
command -v tmux >/dev/null 2>&1 || sudo apt-get install -y tmux   # vLLM 서버를 세션과 분리해 띄우는 용도

echo "== 5. GPU 유휴 자동 중지"
sudo install -m 755 "$REPO_ROOT/infra/gpu/gpu_idle_stop.sh" /usr/local/bin/faers-gpu-idle-stop
sudo tee /etc/systemd/system/faers-gpu-idle-stop.service >/dev/null <<'EOF'
[Unit]
Description=faers-gpu: GPU가 일정 시간 안 쓰이면 인스턴스 중지

[Service]
Type=oneshot
ExecStart=/usr/local/bin/faers-gpu-idle-stop
EOF
sudo tee /etc/systemd/system/faers-gpu-idle-stop.timer >/dev/null <<'EOF'
[Unit]
Description=faers-gpu: 5분마다 GPU 유휴 검사

[Timer]
OnBootSec=10min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now faers-gpu-idle-stop.timer

echo "== 확인"
HF_HOME="$HF_CACHE" "$VENV/bin/python" - <<'PY'
import torch, vllm
print("vllm", vllm.__version__, "| torch", torch.__version__, "| cuda", torch.cuda.is_available(),
      "|", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "-")
PY
aws sts get-caller-identity --query Arn --output text || echo "(aws cli 없음 또는 역할 미연결 — 인스턴스 프로파일 faers-ec2-role 확인)"
systemctl list-timers faers-gpu-idle-stop.timer --no-pager | head -3
echo
echo "완료. 새 터미널부터 환경변수 적용.  다음: bash infra/gpu/fetch_artifacts.sh  →  bash infra/gpu/serve_vllm.sh <모델>"
