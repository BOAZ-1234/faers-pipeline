#!/usr/bin/env bash
# faers-gpu-idle-stop.timer가 5분마다 실행한다. GPU가 IDLE_MINUTES 동안 안 쓰이면 인스턴스를 끈다.
# 인스턴스 종료 동작이 "중지"라 shutdown = stop (삭제되지 않음, 디스크 유지).
#
# 왜 CPU 경보(faers-work의 idle-stop)를 쓰지 않나: vLLM 추론 중에는 CPU가 거의 놀아서 CPU 기준이면 실행 중에 꺼진다.
#
# "사용 중"으로 보는 경우 (하나라도 해당하면 타이머 리셋)
#   - GPU 사용률 >= BUSY_UTIL%  (vLLM이 요청을 처리하는 중)
#   - Session Manager 세션이 열려 있음 (사람이 세팅·디버깅 중. SSM 세션은 20분 무입력이면 끊긴다)
# vLLM 서버가 떠 있기만 하고 요청이 없으면 유휴로 본다 — 켜 두고 잊어버리는 게 가장 비싼 사고라서.
#
# 잠시 끄기:  sudo touch /etc/faers-gpu-idle-stop.disabled   (다시 켜기: sudo rm 같은 파일)
set -u

IDLE_MINUTES="${IDLE_MINUTES:-30}"
BUSY_UTIL="${BUSY_UTIL:-5}"
STATE_DIR="${STATE_DIR:-/var/lib/faers-gpu-idle}"
STATE="$STATE_DIR/idle_since"

log() { logger -t faers-gpu-idle-stop "$*"; echo "$*"; }

if [ -e /etc/faers-gpu-idle-stop.disabled ]; then
  log "비활성화 파일 있음 — 건너뜀"
  rm -f "$STATE"
  exit 0
fi

mkdir -p "$STATE_DIR"
util=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | sort -nr | head -1 | tr -d ' ')
util=${util:-0}
sessions=$(pgrep -fc ssm-session-worker || true)
now=$(date +%s)

if [ "$util" -ge "$BUSY_UTIL" ] || [ "${sessions:-0}" -gt 0 ]; then
  rm -f "$STATE"
  exit 0
fi

[ -s "$STATE" ] || echo "$now" > "$STATE"
idle_min=$(( (now - $(cat "$STATE")) / 60 ))
if [ "$idle_min" -ge "$IDLE_MINUTES" ]; then
  log "GPU 유휴 ${idle_min}분(사용률 ${util}%, 세션 0) — 인스턴스 중지"
  rm -f "$STATE"
  ${SHUTDOWN_CMD:-/sbin/shutdown -h now}
else
  log "GPU 유휴 ${idle_min}/${IDLE_MINUTES}분"
fi
