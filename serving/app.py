"""FAERS signal-scores serving layer.

DuckDB(인메모리) + FastAPI 로 fct_signal_scores 순위표를 저지연 API 로 서빙한다.
Trino/카탈로그/워커 없이 단일 프로세스. 163MB / 3.8M 행은 메모리 상주로 충분.

데이터 소스는 FAERS_SIGNALS_SOURCE 환경변수로 지정:
  - 로컬 파케이:  _probe/data/fct_signal_scores.parquet (기본값)
  - S3:          s3://bucket/key/fct_signal_scores.parquet  (httpfs 자동 로드)

실행:
  uvicorn serving.app:app --host 0.0.0.0 --port 8000
  # 문서: http://localhost:8000/docs
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

import duckdb
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
DEFAULT_SOURCE = str(
    Path(__file__).resolve().parent.parent / "_probe" / "data" / "fct_signal_scores.parquet"
)
SOURCE = os.environ.get("FAERS_SIGNALS_SOURCE", DEFAULT_SOURCE)

# 정렬 허용 컬럼 (화이트리스트 — 문자열 포맷 주입 방지)
ORDERABLE = {
    "ic025": "ic025",
    "ic": "ic",
    "ror": "ror",
    "prr": "prr",
    "a": "a",
}
MAX_LIMIT = 500

# 전역 커넥션 (startup 에서 채움)
_con: Optional[duckdb.DuckDBPyConnection] = None


# ---------------------------------------------------------------------------
# 응답 모델
# ---------------------------------------------------------------------------
class Signal(BaseModel):
    ingredient: Optional[str]
    pt: Optional[str]
    a: Optional[int]
    a_plus_b: Optional[int]
    a_plus_c: Optional[int]
    n: Optional[int]
    ic: Optional[float]
    ic025: Optional[float]
    ror: Optional[float]
    prr: Optional[float]
    solo_case_count: Optional[int]
    combo_case_count: Optional[int]


class Stats(BaseModel):
    rows: int
    ingredients: int
    pts: int
    source: str


# ---------------------------------------------------------------------------
# 앱 수명주기: startup 시 파케이를 인메모리 테이블로 적재 + 인덱스
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _con
    con = duckdb.connect(database=":memory:")
    # S3 소스면 httpfs + aws 로드 후 credential_chain secret 생성.
    # 자격증명은 AWS 기본 체인(env → 프로파일 → EC2 인스턴스 Role)에서 가져오므로
    # EC2(faers-ec2-role)에서 띄우면 키 주입 없이 프라이빗 버킷을 읽는다.
    if SOURCE.startswith("s3://"):
        region = os.environ.get("FAERS_S3_REGION", "ap-northeast-2")
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute("INSTALL aws; LOAD aws;")
        con.execute(
            "CREATE OR REPLACE SECRET faers_s3 "
            f"(TYPE s3, PROVIDER credential_chain, REGION '{region}')"
        )
    con.execute(
        "CREATE TABLE signals AS SELECT * FROM read_parquet(?)",
        [SOURCE],
    )
    # 점/범위 조회 가속용 ART 인덱스
    con.execute("CREATE INDEX idx_ing ON signals(ingredient)")
    con.execute("CREATE INDEX idx_pt ON signals(pt)")
    _con = con
    yield
    con.close()
    _con = None


app = FastAPI(
    title="FAERS Signal Scores API",
    description="FAERS 약물-이상반응 신호 순위표(fct_signal_scores) 서빙 계층",
    version="0.1.0",
    lifespan=lifespan,
)


def _cur() -> duckdb.DuckDBPyConnection:
    if _con is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="store not ready")
    return _con.cursor()  # 요청마다 독립 커서 (스레드 안전)


def _rows_to_signals(cur) -> list[Signal]:
    cols = [d[0] for d in cur.description]
    return [Signal(**dict(zip(cols, r))) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok" if _con is not None else "loading"}


@app.get("/stats", response_model=Stats)
def stats() -> Stats:
    cur = _cur()
    r = cur.execute(
        "SELECT count(*), count(DISTINCT ingredient), count(DISTINCT pt) FROM signals"
    ).fetchone()
    return Stats(rows=r[0], ingredients=r[1], pts=r[2], source=SOURCE)


@app.get("/signals/{ingredient}", response_model=list[Signal])
def signals_by_ingredient(
    ingredient: str,
    order: Literal["ic025", "ic", "ror", "prr", "a"] = "ic025",
    min_a: int = Query(0, ge=0, description="최소 공동보고 건수(a)"),
    min_ic025: Optional[float] = Query(None, description="최소 ic025 (예: 0 = MHRA 게이트)"),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> list[Signal]:
    """한 성분의 PT별 신호를 정렬 기준 내림차순으로 반환."""
    conds = ["ingredient = ?", "a >= ?"]
    params: list = [ingredient, min_a]
    if min_ic025 is not None:
        conds.append("ic025 >= ?")
        params.append(min_ic025)
    sql = (
        f"SELECT * FROM signals WHERE {' AND '.join(conds)} "
        f"ORDER BY {ORDERABLE[order]} DESC NULLS LAST LIMIT ? OFFSET ?"
    )
    params += [limit, offset]
    cur = _cur()
    cur.execute(sql, params)
    out = _rows_to_signals(cur)
    if not out and offset == 0:
        raise HTTPException(status_code=404, detail=f"no signals for ingredient '{ingredient}'")
    return out


@app.get("/pt/{pt}/top", response_model=list[Signal])
def top_ingredients_for_pt(
    pt: str,
    order: Literal["ic025", "ic", "ror", "prr", "a"] = "ic025",
    min_a: int = Query(0, ge=0),
    min_ic025: Optional[float] = Query(None),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> list[Signal]:
    """한 이상반응(PT)에 대해 신호가 강한 성분 상위 목록."""
    conds = ["pt = ?", "a >= ?"]
    params: list = [pt, min_a]
    if min_ic025 is not None:
        conds.append("ic025 >= ?")
        params.append(min_ic025)
    sql = (
        f"SELECT * FROM signals WHERE {' AND '.join(conds)} "
        f"ORDER BY {ORDERABLE[order]} DESC NULLS LAST LIMIT ? OFFSET ?"
    )
    params += [limit, offset]
    cur = _cur()
    cur.execute(sql, params)
    out = _rows_to_signals(cur)
    if not out and offset == 0:
        raise HTTPException(status_code=404, detail=f"no signals for pt '{pt}'")
    return out


@app.get("/pair", response_model=Signal)
def pair(
    ingredient: str = Query(..., description="성분명"),
    pt: str = Query(..., description="이상반응(PT)"),
) -> Signal:
    """성분×PT 단일 쌍의 신호 통계."""
    cur = _cur()
    cur.execute("SELECT * FROM signals WHERE ingredient = ? AND pt = ? LIMIT 1", [ingredient, pt])
    out = _rows_to_signals(cur)
    if not out:
        raise HTTPException(status_code=404, detail="pair not found")
    return out[0]


@app.get("/top", response_model=list[Signal])
def top_signals(
    order: Literal["ic025", "ic", "ror", "prr", "a"] = "ic025",
    min_a: int = Query(3, ge=0, description="기본 3건 — 희소 노이즈 제거"),
    min_ic025: Optional[float] = Query(None),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> list[Signal]:
    """전체에서 신호가 강한 성분×PT 쌍 상위 목록."""
    conds = ["a >= ?"]
    params: list = [min_a]
    if min_ic025 is not None:
        conds.append("ic025 >= ?")
        params.append(min_ic025)
    sql = (
        f"SELECT * FROM signals WHERE {' AND '.join(conds)} "
        f"ORDER BY {ORDERABLE[order]} DESC NULLS LAST LIMIT ? OFFSET ?"
    )
    params += [limit, offset]
    cur = _cur()
    cur.execute(sql, params)
    return _rows_to_signals(cur)
