# infra — 팀 작업 서버와 AWS 권한

로컬 노트북 대신 쓸 **공용 작업 서버(EC2 1대)** 와, 팀원이 S3에 접근하는 권한 구성을 기록한다.
콘솔에서 손으로 만든 것들이라, 무엇을 어떻게 만들었는지 여기에 남겨둔다 (Terraform 등은 쓰지 않는다 — 규모에 비해 과함).

> 결정: **S3 → Cloudflare R2 이관은 하지 않는다.** 실측 데이터가 원본 zip 3.7GB / Iceberg 3.6GB라 비용 차이가 없고,
> B단계에서 컴퓨트가 AWS 안으로 들어가면 같은 리전 S3 전송비는 0이라 R2의 이점(egress 무료)이 사라진다.

## 구성 (AWS 계정 825494477740, 서울 ap-northeast-2)

| 리소스 | 이름 | 용도 |
|---|---|---|
| Budgets | `boaz-faers-projects` | 월 $15 예산 알림 |
| IAM 정책 | `faers-team-s3` | S3 읽기 + `stage_b_silver`만 쓰기 (bronze는 읽기 전용) — [`policies/faers-team-s3.json`](policies/faers-team-s3.json) |
| IAM 정책 | `faers-team-ec2` | 작업 서버 1대 시작/중지/SSM 접속 — [`policies/faers-team-ec2.json`](policies/faers-team-ec2.json) |
| IAM 정책 | `faers-team-selfkeys` | 팀원이 **본인** 액세스 키만 만들고 지울 수 있음 (`${aws:username}`으로 본인 계정에 한정) — [`policies/faers-team-selfkeys.json`](policies/faers-team-selfkeys.json) |
| IAM 그룹 | `faers-team` | 위 세 정책 연결, 팀원 사용자를 여기에 넣는다 |
| IAM 역할 | `faers-ec2-role` | 서버가 쓰는 역할: `faers-team-s3` + `AmazonSSMManagedInstanceCore` |
| EC2 | `faers-work` (`i-09ea3552c292bd78c`) | m5.xlarge(4코어/16GB), Amazon Linux 2023, gp3 30GiB |
| CloudWatch 경보 | `faers-work-idle-stop` | CPU 평균 3% 미만이 2시간 이어지면 서버 자동 중지 |

기존 `boaz_1234_user`(액세스 키 방식)는 파이프라인용으로 남아 있다. 팀원이 새 방식으로 옮겨간 뒤 **키를 교체(rotate)** 한다.

## 서버 사용법 (팀원)

1. 콘솔 로그인 → 우측 상단 리전 **서울** → EC2 → 인스턴스 → `faers-work` 선택
2. **인스턴스 상태 → 인스턴스 시작** (2~3분 대기, 상태 검사 2/2)
3. **연결 → Session Manager 탭 → 연결**. `EC2 Instance Connect` 탭은 SSH라 **동작하지 않는다** (22번 포트를 열지 않았다)
4. 터미널이 열리면 **작업 시작 준비** (접속할 때마다):
   ```bash
   cd ~                                    # 접속 직후 위치에는 쓰기 권한이 없어서 먼저 홈으로 이동
   source /opt/faers-venv/bin/activate     # 가상환경 켜기 — 프롬프트 앞에 (faers-venv) 가 붙어야 pyspark 사용 가능
   mkdir -p ~/본인이름 && cd ~/본인이름      # 본인 폴더에서만 작업
   git clone https://github.com/BOAZ-1234/faers-pipeline.git   # 최초 1회 (공개 레포라 인증 불필요)
   cd faers-pipeline && git pull           # 이후에는 pull 로 최신화
   ```
5. 끝나면 `exit`. 다른 사람이 쓰는 중이 아니면 **인스턴스 상태 → 인스턴스 중지** (안 눌러도 자동 중지되지만 그 동안 요금이 나간다)

**종료(terminate)는 절대 누르지 않는다** — 서버가 삭제된다. 권한상 팀원은 못 누르게 막혀 있다.

### 주의
- 모두 같은 OS 계정(`ssm-user`)으로 들어오므로 홈 폴더가 공유된다. **`~/본인이름/` 폴더에서만 작업**한다.
- 메모리 16GB를 나눠 쓴다. 8,400만 행급 집계는 한 번에 한 명씩.
- 서버는 **인스턴스 역할**로 S3에 접근한다. 서버에 액세스 키를 넣거나 `.env`를 만들지 않는다.
- 요금: 시간당 약 $0.24 (m5.xlarge, 서울, Linux 온디맨드). 월 예산 $15 ≈ 60시간. 중지하면 서버 요금은 멈추고 디스크(30GiB)만 과금된다.

## 로컬 터미널에서 서버 접속 (선택, 액세스 키 필요)

브라우저 터미널이 불편하면 로컬에서 접속할 수 있다. 액세스 키(프로그램용 아이디/비밀번호)가 필요하고, **본인이 직접 만든다** (비밀 키가 채팅 등에 오가지 않게).

1. 콘솔 우측 상단 본인 이름 → **보안 자격 증명 → 액세스 키 만들기** → 사용 사례 **CLI** → 생성
2. **Secret Access Key는 이 화면에서 한 번만 보인다.** 바로 `aws configure`에 붙여넣는다 (리전 `ap-northeast-2`)
3. 로컬에 **AWS CLI v2**와 **Session Manager 플러그인**을 설치한다
4. 확인: `aws sts get-caller-identity` → 본인 사용자 이름이 나오면 정상
5. 접속: `aws ssm start-session --target i-09ea3552c292bd78c`

규칙:
- 키를 **채팅, 레포, `.env` 커밋에 넣지 않는다** (공개 레포). `aws configure`는 `~/.aws/credentials`에 저장하므로 레포와 무관하다.
- 유출이 의심되면 IAM에서 그 키를 즉시 **비활성화**한다. 사용자당 키는 최대 2개.
- 로컬에서 키로 S3를 대량으로 읽으면 전송비가 다시 생긴다. **무거운 작업은 서버에서** 하고 로컬은 접속용으로만 쓴다.
- `ssm:SendCommand`(터미널 없이 서버에 명령 전송)는 필요할 때 범위를 서버 1대로 좁혀 추가한다. 지금은 열지 않았다.

## 서버 최초 세팅

**최초 세팅은 2026-09-19에 끝냈다** (Java 17, Python 3.11, `/opt/faers-venv`에 pyspark 3.5.1). 디스크에 남으므로 서버를 중지/시작해도 다시 할 필요 없다.
서버를 새로 만들었을 때만 [`ec2/setup.sh`](ec2/setup.sh)를 실행한다 (여러 번 실행해도 안전).

```bash
cd ~                              # 접속 직후 위치는 쓰기 금지 폴더라 먼저 홈으로 이동
bash setup.sh
```

**새로 접속할 때마다** 가상환경을 켜야 pyspark를 쓸 수 있다 (프롬프트 앞에 `(faers-venv)`가 붙으면 켜진 것).

```bash
source /opt/faers-venv/bin/activate
aws sts get-caller-identity       # Arn 에 assumed-role/faers-ec2-role 이 보이면 정상
```

검증 결과(9/19): 서버에서 키 없이 `build_spark()`가 인스턴스 역할로 Iceberg 테이블 조회까지 성공했다.
팀원 계정(`faers-team`)으로 서버 시작, Session Manager 접속, 역할 확인까지 확인했다.

**코드는 각자 폴더에 clone해서 쓴다.** 레포가 공개라 인증이 필요 없다. 서버는 **실행용**이고, 코드 수정과 push는
각자 노트북에서 한다 (서버에는 GitHub 토큰이나 키를 두지 않는다 — 여럿이 같은 계정으로 접속하는 공유 서버다).
공개 레포이므로 `.env`, 액세스 키, 데이터, 서버에서 만든 개인 산출물은 **절대 커밋하지 않는다.**

## 코드에서 S3 접근

`bronze/loaders/spark_session.py`의 `build_spark()`를 쓴다. 인증을 환경에 맞게 고른다.

```python
# bronze/loaders/ 안에서 실행하는 스크립트는 바로:
from spark_session import build_spark
# 다른 폴더(diagnostics/, handoff/, verification/ 등)에서는 경로를 먼저 잡아준다:
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "loaders"))
from spark_session import build_spark

spark = build_spark("MyJob", driver_memory="8g")
```

- `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`가 있으면(로컬 `bronze/.env`) 그 키를 쓴다. `AWS_SESSION_TOKEN`도 있으면 임시 자격증명으로 처리한다.
- 없으면(EC2) 인스턴스 역할로 자동 인증한다.
- 기존 스크립트는 세션 설정이 각자 복붙돼 있어서, EC2에서 돌리려면 이 함수로 바꿔야 한다. 지금은 `extract_unique_drugs_to_silver.py`만 옮겼고 나머지는 쓰는 시점에 하나씩 옮긴다.

`bronze/CLAUDE.md`의 조회 비용 규칙(기본 10건, 그 이상은 확인 후)은 서버에서도 그대로 적용된다.

## 아직 안 한 것
- `boaz_1234_user` 키 교체 (팀원 전환 후)
- 팀원별 OS 계정 분리 (SSM Run As) — 필요해지면
- B단계 컴퓨트(EMR Serverless)와 Glue Catalog 전환 — B 시작 직전. 지금 `hadoop` 카탈로그는 동시 쓰기에 안전하지 않다
- GPU — C단계 3단계 커버리지 측정 결과가 나온 뒤에 판단
