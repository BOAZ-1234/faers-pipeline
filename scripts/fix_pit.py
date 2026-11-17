"""finish_pit 버그 수정판 — _crq(반응 매칭) 0 문제.

진단: _cdq(성분) 134,957 OK인데 _crq(반응) 0. gold_pt 반응명과 fact.pt 표기 불일치
(성분은 둘 다 소문자라 매칭됐고, pt 는 raw faers_reac 원본표기 vs gold 정규화표기).
→ pt 매칭을 **대소문자/공백 무시**로 교체.

무거운 _base/_caseq/_cdq 는 d_pit.duckdb 에 이미 있으니 **재사용**하고 _crq 부터만 다시 돈다(~2분).
시작 시 gold_pt vs fact.pt 샘플·매칭수를 찍어, 대소문자가 아니어도 한 번에 판단되게 한다.

실행(EC2):
  nohup python3 ~/fix_pit.py > ~/fix_pit.log 2>&1 & echo STARTED
결과: ~/first_detection.csv + S3 자동 업로드.
"""

from __future__ import annotations

import os
import time
import subprocess

HOME = os.path.expanduser("~")
DB = f"{HOME}/d_pit.duckdb"
OUT = f"{HOME}/first_detection.csv"
TMP = f"{HOME}/duckdb_tmp"
DST = ("s3://boaz-1234-825494477740-ap-northeast-2-an/"
       "iceberg_warehouse/stage_c_silver/stage3/first_detection.csv")


def main() -> int:
    import duckdb

    os.makedirs(TMP, exist_ok=True)
    con = duckdb.connect(DB)
    for s in ["SET memory_limit='12GB'", f"SET temp_directory='{TMP}'",
              "SET preserve_insertion_order=false"]:
        con.execute(s)

    t0 = time.time()

    def log(m: str) -> None:
        print(f"[{time.time()-t0:5.0f}s] {m}", flush=True)

    # 재사용 테이블 확인
    for t in ("_base", "_caseq", "_cdq", "gold_pt"):
        n = con.execute(f"select count(*) from {t}").fetchone()[0]
        log(f"재사용 {t}: {n:,}")

    # 진단 출력 (대소문자가 원인인지 바로 확인)
    log("진단: gold_pt / fact.pt 샘플 + 매칭수")
    print("  GOLD_PT", con.execute("select pt from gold_pt limit 6").fetchall(), flush=True)
    print("  FACT_PT", con.execute(
        "select distinct pt from _base where pt is not null and pt<>'' limit 6"
    ).fetchall(), flush=True)
    exact = con.execute(
        "select count(*) from (select distinct pt from _base) b "
        "join gold_pt g on b.pt=g.pt").fetchone()[0]
    ci = con.execute(
        "select count(*) from (select distinct pt from _base) b "
        "join gold_pt g on upper(trim(b.pt))=upper(trim(g.pt))").fetchone()[0]
    log(f"  EXACT 매칭 {exact} / CI(대소문자무시) 매칭 {ci}")

    # _crq 재생성 — 대소문자/공백 무시
    log("_crq 재생성 (case-insensitive) ...")
    con.execute("""CREATE OR REPLACE TABLE _crq AS
        select cr.pt, cr.caseid, cq.q
        from (select distinct caseid, pt from _base
              where pt is not null and pt<>''
                and upper(trim(pt)) in (select upper(trim(pt)) from gold_pt)) cr
        join _caseq cq using(caseid)""")
    ncrq = con.execute("select count(*) from _crq").fetchone()[0]
    log(f"  _crq {ncrq:,}")
    if ncrq == 0:
        log("여전히 0 — 대소문자가 원인이 아님. 위 GOLD_PT/FACT_PT 샘플 보고 다시 판단 필요.")
        return 3

    # _co
    log("_co (공출현) ...")
    con.execute("""CREATE OR REPLACE TABLE _co AS
        select cdq.ingredient, crq.pt, cdq.caseid, cdq.q
        from _cdq cdq join _crq crq using(caseid)""")
    log(f"  _co {con.execute('select count(*) from _co').fetchone()[0]:,}")

    # 격자
    lo, hi = con.execute("select min(q), max(q) from _caseq").fetchone()
    lo, hi = int(lo), int(hi)
    con.execute(f"""CREATE OR REPLACE TABLE _grid AS
        select cast(g*4+3 as integer) as Q
        from generate_series({lo // 4}, {hi // 4}) as t(g)""")

    # 누적 marginals (각 엔티티 안 caseid 유일 → count(*) = distinct)
    log("누적 집계 (a / ab / ac / N) ...")
    con.execute("""CREATE OR REPLACE TABLE _nq AS
        with nh as (select q, count(*) c from _caseq group by q)
        select g.Q, coalesce((select sum(c) from nh where nh.q<=g.Q),0) as n
        from _grid g""")
    con.execute("""CREATE OR REPLACE TABLE _abQ AS
        select cdq.ingredient, g.Q, count(*) ab
        from _cdq cdq join _grid g on cdq.q<=g.Q group by 1,2""")
    con.execute("""CREATE OR REPLACE TABLE _acQ AS
        select crq.pt, g.Q, count(*) ac
        from _crq crq join _grid g on crq.q<=g.Q group by 1,2""")
    con.execute("""CREATE OR REPLACE TABLE _aQ AS
        select co.ingredient, co.pt, g.Q, count(*) a
        from _co co join _grid g on co.q<=g.Q group by 1,2,3""")
    log(f"  _aQ {con.execute('select count(*) from _aQ').fetchone()[0]:,}행")

    # 게이트 + 최초탐지 분기
    log("게이트 + 최초탐지 분기 ...")
    con.execute("""CREATE OR REPLACE TABLE first_detection AS
        with passed as (
          select a.ingredient, a.pt, a.Q
          from _aQ a
          join _abQ b on a.ingredient=b.ingredient and a.Q=b.Q
          join _acQ c on a.pt=c.pt and a.Q=c.Q
          join _nq  n on a.Q=n.Q
          where a.a >= 3 and n.n > 0
            and log2((a.a+0.5)/((b.ab*c.ac*1.0/n.n)+0.5))
                - 3.3*power(a.a+0.5,-0.5) - 2*power(a.a+0.5,-1.5) > 0)
        select ingredient, pt, min(Q) as first_q from passed group by 1,2""")
    n = con.execute("select count(*) from first_detection").fetchone()[0]
    con.execute(f"COPY first_detection TO '{OUT}' (HEADER)")
    con.execute("CHECKPOINT")
    log(f"DONE  first_detection {n:,}행 -> {OUT}")

    r = subprocess.run(["aws", "s3", "cp", OUT, DST, "--region", "ap-northeast-2"],
                       capture_output=True, text=True)
    log(f"S3 업로드 rc={r.returncode} {r.stderr.strip()[-200:]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
