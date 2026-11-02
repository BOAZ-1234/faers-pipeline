# GPU 서버 사용 가이드 (`faers-gpu`)

C단계 3단계(이름 통일) **LLM 판정**을 돌리는 서버. 처음 쓰는 사람도 이 문서만 보고 접속 → 실험 → 종료까지 할 수 있게 쓴다.

| | |
|---|---|
| 인스턴스 | `faers-gpu` (`i-08b3cf5ddb7dadc21`), g6.xlarge, **NVIDIA L4 24GB**, RAM 16GB |
| OS | Deep Learning OSS Nvidia Driver AMI (Ubuntu 26.04) |
| 파이썬 | `/opt/faers-gpu-venv` (Python 3.12, vLLM 0.33.0, torch 2.14) — OS 파이썬 쓰지 않음 |
| 권한 | 역할 `faers-ec2-role` (S3 읽기, `stage3/` 쓰기). 키 없음 |
| 접속 | Session Manager만 (SSH·인바운드 포트 없음) |
| 예산 | Budgets `faers-gpu` **월 $50 (팀 전체)** |

---

## 0. 비용 규칙 — 먼저 읽기

- **켜져 있는 동안 시간당 과금**된다. 정확한 단가는 EC2 콘솔 인스턴스 유형 화면에서 확인. 디스크(100GiB)는 **꺼져 있어도** 과금(서울 gp3 기준 월 약 $9).
- **다 쓰면 직접 중지한다.** 자동 중지(§7)는 안전장치일 뿐이다.
- **종료(terminate)는 누르지 않는다** — 서버와 세팅이 통째로 삭제된다(종료 방지 켜져 있음).
- **GPU는 한 대·한 장이다.** vLLM은 한 번에 한 모델만 올라간다. 쓰기 전에 팀 채팅에 "GPU 씁니다(모델, 예상 시간)"를 남기고, 다른 사람이 띄운 vLLM을 끄지 않는다.

---

## 1. 켜기·접속

1. AWS 콘솔(리전 **서울**) → EC2 → 인스턴스 → `faers-gpu` 선택
2. **인스턴스 상태 → 인스턴스 시작** → 상태 검사 통과까지 3~5분
3. **연결 → Session Manager → 연결** ("연결"이 회색이면 1~2분 더 기다림)
4. 터미널이 열리면 **가장 먼저**:
   ```bash
   bash -l
   ```
   Session Manager 기본 셸은 `sh`라 `source`가 없고 환경변수(모델 캐시 위치·vLLM 주소)가 안 읽힌다. `bash -l` 후에 작업한다.

> **명령은 한 줄씩 붙여넣고 Enter.** 브라우저 터미널에 여러 줄을 한 번에 붙이면 줄이 이어 붙어
> `aws sts get-caller-identityaws` 같은 오류가 난다.

---

## 2. 처음 한 번 (사람마다)

```bash
cd ~ && mkdir -p ~/본인이름 && cd ~/본인이름
```
```bash
git clone https://github.com/BOAZ-1234/faers-pipeline.git
```
```bash
cd faers-pipeline && source /opt/faers-gpu-venv/bin/activate
```
```bash
bash infra/gpu/fetch_artifacts.sh
```

`fetch_artifacts.sh`는 사전·3단계 입력·FAISS 인덱스(약 450MB)를 Release에서 받아 정해진 위치에 둔다(이미 있으면 건너뜀).

**DiAna dev 정답지는 따로 둔다** — `silver/drug_dict/golden/output/diana_golden_dev.csv`.
팀 공용 파일(함하경 생성본, dev 10,895쌍·seed 42·신고 ≥200건)을 써야 점수가 비교된다. 새로 만들면 다른 정답지가 된다.

> 서버 자체 세팅(`setup_gpu.sh`)은 **서버를 새로 만들었을 때만** 한다. 이미 끝나 있다(2026-11-02).

---

## 3. 매번 작업 시작

```bash
bash -l
```
```bash
cd ~/본인이름/faers-pipeline && git pull && source /opt/faers-gpu-venv/bin/activate
```
```bash
nvidia-smi
```

`nvidia-smi` 아래 **Processes**에 `VLLM`/`python`이 보이면 **누군가 쓰는 중**이다. 팀 채팅 확인 후 진행.

---

## 4. vLLM 서버 띄우기

tmux 안에서 띄워야 접속을 끊어도 서버가 유지된다.

```bash
tmux new -s vllm
```
```bash
cd ~/본인이름/faers-pipeline
```
```bash
bash infra/gpu/serve_vllm.sh Qwen/Qwen2.5-7B-Instruct-AWQ
```

- 처음 쓰는 모델은 내려받느라 3~5분(이후엔 `/opt/hf-cache`에 남아 1분 안팎).
- **`Application startup complete`** 가 나오면 준비 완료 → **Ctrl+B 누르고 뗀 뒤 D** 로 tmux에서 빠져나온다(서버는 계속 돔).
- 확인: `curl -s localhost:8000/v1/models` → 모델 이름이 든 JSON

| 하고 싶은 것 | 명령 |
|---|---|
| vLLM 화면 다시 보기 | `tmux attach -t vllm` |
| 모델 바꾸기 | `tmux attach -t vllm` → **Ctrl+C** → 다른 모델로 `serve_vllm.sh` 재실행 |
| vLLM 끄기 | `tmux attach -t vllm` → **Ctrl+C** → `exit` |
| 문맥 길이 늘리기 | `MAX_MODEL_LEN=8192 bash infra/gpu/serve_vllm.sh <모델>` |

비교 대상 모델(10/22 회의): `Qwen/Qwen2.5-3B-Instruct`, `Qwen/Qwen2.5-7B-Instruct-AWQ`, `Qwen/Qwen2.5-14B-Instruct-AWQ` — L4 24GB에 각각 단독으로 올라간다.

---

## 5. 실험 돌리기

vLLM을 띄운 뒤, tmux 밖(일반 터미널)에서 레포 루트 기준.

**① 3단계 입력 만들기 (triage)** — 사전이 바뀌지 않았으면 한 번만
```bash
python silver/drug_dict/triage.py silver/drug_dict/unmatched_for_stage3.csv --out-unknown-csv out/unknown.csv
```

**② 판정 (하네스)** — 프롬프트 비교는 dev 정답지에 있는 이름만(`--gold`)
```bash
CUDA_VISIBLE_DEVICES= python silver/drug_dict/stage3_harness.py run out/unknown.csv --out out/<실행이름> --judge openai --model Qwen/Qwen2.5-7B-Instruct-AWQ --prompt silver/drug_dict/prompts/stage3_judge_<이름>.txt --gold silver/drug_dict/golden/output/diana_golden_dev.csv
```
- `CUDA_VISIBLE_DEVICES=` — 후보 검색용 임베딩(SapBERT)을 **CPU**로 돌린다. GPU 메모리 90%를 vLLM이 쓰고 있어 같이 올리면 부족할 수 있다.
- `--prompt` 파일 이름(확장자 뺀 것)이 결과에 `prompt_version`으로 남는다. 본인 프롬프트는 `silver/drug_dict/prompts/stage3_judge_<이름>.txt`.
- `--limit 20`을 붙이면 빠른 확인용.
- `--concurrency 16` 등으로 동시 요청 수를 올리면 빨라진다(운영점은 파일럿에서 결정).

**③ 채점**
```bash
python silver/drug_dict/stage3_harness.py evaluate --out out/<실행이름> --gold silver/drug_dict/golden/output/diana_golden_dev.csv --details-csv out/<실행이름>/details.csv
```
`auto_precision`(붙인 것 중 정답 비율, 목표 ≥ 99%), `none_rate`, `cand_recall`(후보 안에 정답이 있었던 비율)이 나온다. 오답은 `details.csv`에서 본다.

**④ 결과 표로 정리 (선택)**
```bash
python silver/drug_dict/stage3_harness.py finalize --out out/<실행이름> --dest out/<실행이름>/final
```

**결과를 S3에 남기기** — `--out s3://boaz-1234-825494477740-ap-northeast-2-an/stage3/<실행이름>` 로 주면 part가 S3에 쌓인다(서버 디스크와 무관하게 보존, 중단돼도 같은 명령 재실행 시 이어서 함). `stage3/`은 쓰기만 되고 삭제는 안 된다.

---

## 6. 끝내기

1. vLLM 끄기: `tmux attach -t vllm` → **Ctrl+C** → `exit`
2. 터미널 `exit`
3. 콘솔 → `faers-gpu` → **인스턴스 상태 → 인스턴스 중지**
4. 팀 채팅에 "GPU 끝났습니다"

---

## 7. 자동 중지

5분마다 검사해서 **GPU 사용률 5% 미만 + Session Manager 접속 없음**이 **30분** 이어지면 서버가 꺼진다(삭제 아님).

- vLLM만 띄워 두고 요청이 없으면 **유휴로 본다** — 켜 두고 잊는 게 가장 비싼 사고라서.
- 판정이 돌고 있으면 GPU 사용률이 올라가 꺼지지 않는다. 접속 중에도 꺼지지 않는다.

```bash
journalctl -t faers-gpu-idle-stop -n 20          # 판단 기록
sudo touch /etc/faers-gpu-idle-stop.disabled     # 잠시 끄기(긴 다운로드 등). 다시 켜기: sudo rm /etc/faers-gpu-idle-stop.disabled
```

꺼지면 tmux의 vLLM도 같이 꺼진다. 다시 쓰려면 §1부터.

---

## 8. 본 실행 (Spot)

본 실행(운영점 `n_reports ≥ 2` = **132,450 entity**, 재정규화 반영 후)은 g5.xlarge **Spot**으로 싸게 돌린다.

1. `faers-gpu`가 원하는 상태(모델 캐시 포함)일 때 → **작업 → 이미지 및 템플릿 → 이미지 생성** (이름 `faers-gpu-ami-YYYYMMDD`)
2. 그 AMI로 **인스턴스 시작**: 유형 **g5.xlarge**(A10G 24GB), 역할 `faers-ec2-role`, 보안 그룹 `faers-gpu-sg`, 키 페어 없음, **태그 `Project=faers-pipeline`**(이게 있어야 팀원이 켜고 끔), 고급 → 구매 옵션 **스팟 인스턴스**
3. 접속 → §3·§4 → 하네스를 **`--out s3://…/stage3/<본실행이름>`** 로 실행
4. Spot은 **2분 예고 후 회수**될 수 있다. 회수되면 새 Spot을 띄워 **같은 명령을 다시 실행** → S3에 저장된 part의 entity는 건너뛰고 이어서 한다(손실은 최대 한 청크 200개).
5. 끝나면 Spot 인스턴스는 **종료**해도 된다(결과는 S3에 있음). `faers-gpu` 원본은 중지 상태로 둔다.

---

## 9. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `source: not found`, 환경변수 비어 있음 | Session Manager가 `sh`로 열림 | `bash -l` |
| `get-caller-identityaws` 같은 이상한 명령 | 여러 줄 붙여넣기로 줄이 이어 붙음 | 한 줄씩 붙여넣기 |
| vLLM `Could not find nvcc ... cuda_home='/usr/local/cuda' doesn't exist` | FlashInfer 샘플러가 커널을 컴파일하려는데 AMI에 nvcc 없음 | `serve_vllm.sh`가 기본으로 끔(`VLLM_USE_FLASHINFER_SAMPLER=0`). 직접 `vllm serve` 하지 말고 스크립트 사용 |
| `FlashInfer kernels are disabled` 경고 | 위와 같은 이유 | 무시해도 동작함(속도만 약간 손해) |
| vLLM `Engine core initialization failed` | 위 에러가 원인이거나, 앞 실행의 잔여 프로세스가 GPU를 잡고 있음 | `grep -nE "Error\|Exception" ~/vllm.log \| tail -5`로 원인 확인. 잔여 프로세스면 `pkill -f "vllm serve"` 후 재실행 (로그 남기기: `... serve_vllm.sh <모델> 2>&1 \| tee ~/vllm.log`) |
| `CUDA out of memory` | 다른 vLLM이 떠 있거나 하네스 임베딩이 GPU를 씀 | `nvidia-smi`로 확인, 하네스 앞에 `CUDA_VISIBLE_DEVICES=` |
| 하네스 `사전 버전 불일치` / `사전 파일 없음` | 사전이 옛 버전이거나 경로가 다름 | `bash infra/gpu/fetch_artifacts.sh` (경로는 `silver/drug_dict/drug_ingredient_map.csv`) |
| `Connection refused` (localhost:8000) | vLLM이 아직 기동 중이거나 꺼짐 | `tmux attach -t vllm`으로 상태 확인 |
| 작업 중 서버가 꺼짐 | 자동 중지(30분 유휴) | §7 기록 확인. 긴 대기 작업이면 비활성화 파일로 잠시 끄기 |
| 팀원이 시작/접속 버튼이 안 됨 | 인스턴스에 태그 `Project=faers-pipeline` 없음 | 관리자가 태그 추가(`faers-team-ec2` 정책이 태그 기준) |

---

## 서버 새로 만들 때 (관리자)

1. EC2 인스턴스 시작: AMI `Deep Learning OSS Nvidia Driver AMI GPU PyTorch`(Ubuntu), g6.xlarge, 키 페어 없음, 보안 그룹 인바운드 0개, gp3 100GiB, 역할 `faers-ec2-role`, 종료 방지 켜기, 종료 동작 "중지", 태그 `Project=faers-pipeline`
2. Session Manager 접속 → `bash -l` → 레포 clone → `bash infra/gpu/setup_gpu.sh` (10~20분)
   - uv로 Python 3.12 venv(`/opt/faers-gpu-venv`) + vLLM + 하네스 의존성, `/opt/hf-cache`, `/etc/profile.d/faers-gpu.sh`, 자동 중지 타이머
3. 확인 출력: `vllm … | cuda True | NVIDIA …`, `assumed-role/faers-ec2-role`
