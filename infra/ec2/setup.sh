#!/usr/bin/env bash
# 작업 서버(Amazon Linux 2023) 최초 1회 세팅. 여러 번 실행해도 안전하다.
#   Session Manager 로 접속한 뒤:  bash setup.sh
# 하는 일: Java 17, Python, 공용 가상환경(/opt/faers-venv), 파이썬 패키지 설치.
# 하지 않는 일: 레포 clone (사용자별 폴더에서 각자 한다, infra/README.md 참고), 키 저장 (서버는 인스턴스 역할로 S3에 접근).
set -euo pipefail

sudo dnf install -y git java-17-amazon-corretto-headless

# Amazon Linux 2023 기본 python3 는 3.9 다. 3.11 이 있으면 그걸 쓴다 (pyspark 3.5 는 둘 다 지원).
if sudo dnf install -y python3.11 python3.11-pip >/dev/null 2>&1; then
  PY=python3.11
else
  sudo dnf install -y python3-pip
  PY=python3
fi
echo "python: $($PY --version)"

VENV=/opt/faers-venv
if [ ! -d "$VENV" ]; then
  sudo "$PY" -m venv "$VENV"
fi
# 로컬 개발 환경과 같은 pyspark 3.5.1 로 맞춘다 (Iceberg 1.5.0 / Spark 3.5 조합)
sudo "$VENV/bin/pip" install --quiet --upgrade pip
sudo "$VENV/bin/pip" install --quiet "pyspark==3.5.1" python-dotenv pandas pyarrow requests pytest
sudo chmod -R a+rX "$VENV"

java -version 2>&1 | head -1
echo
echo "완료. 사용:  source $VENV/bin/activate"
echo "S3 접근 확인:  aws sts get-caller-identity   (assumed-role/faers-ec2-role 이 나오면 정상)"
