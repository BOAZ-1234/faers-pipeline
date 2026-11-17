"""v2 PIT 리드타임 — 단일 패스 마무리 (EC2).

기존 ~/d_pit.duckdb 안의 fact(598M, 재빌드 불필요) + gold_ing/gold_pt 를 재사용해서,
40회 cutoff 스캔 없이 **한 번에** 각 (ingredient,pt)의 최초 게이트통과 분기(first_q)를 뽑는다.

핵심 아이디어 (40스캔 → 몇 번의 스캔):
  - 케이스별 최초 신고 분기 caseq = min(linq)  → "분기 Q까지 등장한 케이스" = caseq.q<=Q
  - 성분총 ab(ing,Q)/반응총 ac(pt,Q)/공출현 a(ing,pt,Q)/전체 N(Q) 는 전부
    "q<=Q 인 distinct 케이스 누적수". 각 엔티티 안에서 케이스는 제 분기에 1번만 등장하므로
    count(distinct caseid) = count(*)  → distinct 비용도 없음.
  - 각 (ing,pt)에 대해 게이트(a>=3 & IC025>0) 처음 통과하는 Q = first_q.

IC025 = log2((a+0.5)/(E+0.5)) - 3.3·(a+0.5)^-0.5 - 2·(a+0.5)^-1.5,  E=ab·ac/N.
게이트·공식·분기정의(선형 year*4+분기)는 build_first_detection.py 의 _agg_sql 과 동일.

최신 caseversion 은 cutoff별이 아니라 **전체 최신 1회**로 근사(속도) — v2 첫 수치엔 충분.

실행(EC2):
  nohup python3 ~/finish_pit.py > ~/finish_pit.log 2>&1 &
  tail -f ~/finish_pit.log      # 단계별 진행
결과: ~/first_detection.csv  (ingredient, pt, first_q)
"""

from __future__ import annotations

import os
import time

HOME = os.path.expanduser("~")
DB = f"{HOME}/d_pit.duckdb"
OUT = f"{HOME}/first_detection.csv"
TMP = f"{HOME}/duckdb_tmp"
MEM = "12GB"


def main() -> int:
    import duckdb

    os.makedirs(TMP, exist_ok=True)
    con = duckdb.connect(DB)
    for s in [f"SET memory_limit='{MEM}'", f"SET temp_directory='{TMP}'",
              "SET preserve_insertion_order=false"]:
        con.execute(s)

    t0 = time.time()

    def log(m: str) -> None:
        print(f"[{time.time()-t0:5.0f}s] {m}", flush=True)

    # 사전 점검: 필요한 테이블 존재 확인
    tabs = {r[0] for r in con.execute(
        "select table_name from information_schema.tables").fetchall()}
    for need in ("fact", "gold_ing", "gold_pt"):
        if need not in tabs:
            print(f"ERROR: 테이블 '{need}' 없음. d_pit.duckdb 확인 필요.", flush=True)
            return 2
    nfact = con.execute("select count(*) from fact").fetchone()[0]
    ni = con.execute("select count(*) from gold_ing").fetchone()[0]
    npt = con.execute("select count(*) from gold_pt").fetchone()[0]
    log(f"재사용: fact {nfact:,}행 / gold 성분 {ni} / gold 반응 {npt}")

    # 1) 전체 최신 caseversion 행만 (full 1회 스캔)
    log("1/7 _base (최신 caseversion) ...")
    con.execute("""CREATE OR REPLACE TABLE _base AS
        with lv as (select caseid, max(caseversion) cv from fact group by caseid)
        select f.caseid, f.role_cod, f.ingredient_set, f.pt, f.linq
        from fact f join lv on f.caseid=lv.caseid and f.caseversion=lv.cv
        where f.linq is not null""")
    log(f"    _base {con.execute('select count(*) from _base').fetchone()[0]:,}행")

    # 2) 케이스별 최초 신고 분기
    log("2/7 _caseq (케이스별 최초 분기) ...")
    con.execute("""CREATE OR REPLACE TABLE _caseq AS
        select caseid, min(linq) q from _base group by caseid""")
    ncase = con.execute("select count(*) from _caseq").fetchone()[0]
    log(f"    케이스 {ncase:,}")

    # 3) gold 성분/반응별 (caseid, 분기)  — distinct (엔티티,caseid)
    log("3/7 _cdq / _crq (gold 성분·반응별 케이스) ...")
    con.execute("""CREATE OR REPLACE TABLE _cdq AS
        select cd.ingredient, cd.caseid, cq.q
        from (select distinct caseid, trim(ing) as ingredient
              from _base, unnest(string_split(ingredient_set,'|')) as t(ing)
              where role_cod in ('PS','SS')
                and ingredient_set is not null and ingredient_set<>''
                and trim(ing) in (select ingredient from gold_ing)) cd
        join _caseq cq using(caseid)""")
    con.execute("""CREATE OR REPLACE TABLE _crq AS
        select cr.pt, cr.caseid, cq.q
        from (select distinct caseid, pt from _base
              where pt is not null and pt<>''
                and pt in (select pt from gold_pt)) cr
        join _caseq cq using(caseid)""")
    log(f"    _cdq {con.execute('select count(*) from _cdq').fetchone()[0]:,} / "
        f"_crq {con.execute('select count(*) from _crq').fetchone()[0]:,}")

    # 4) 공출현 (ingredient, pt, caseid, 그 케이스 분기) — (ing,pt,caseid) 유일
    log("4/7 _co (공출현) ...")
    con.execute("""CREATE OR REPLACE TABLE _co AS
        select cdq.ingredient, crq.pt, cdq.caseid, cdq.q
        from _cdq cdq join _crq crq using(caseid)""")
    log(f"    _co {con.execute('select count(*) from _co').fetchone()[0]:,}")

    # 5) cutoff 격자 = 각 연도 Q4 (build_first_detection 와 동일, 선형 분기)
    lo, hi = con.execute("select min(q), max(q) from _caseq").fetchone()
    lo, hi = int(lo), int(hi)
    con.execute(f"""CREATE OR REPLACE TABLE _grid AS
        select cast(g*4+3 as integer) as Q
        from generate_series({lo // 4}, {hi // 4}) as t(g)""")
    ng = con.execute("select count(*), min(Q), max(Q) from _grid").fetchone()
    log(f"5/7 격자 {ng[0]}개 ({ng[1]}..{ng[2]})")

    # 6) 분기별 누적 marginals (각 엔티티 안 caseid 유일 → count(*) = distinct)
    log("6/7 누적 집계 (a / ab / ac / N) ...")
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
    log(f"    _aQ {con.execute('select count(*) from _aQ').fetchone()[0]:,}행")

    # 7) 게이트 통과한 (ing,pt,Q) 중 최초 Q
    log("7/7 게이트 + 최초탐지 분기 ...")
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

    # 결과 자동 업로드 (인스턴스 role). 사람이 수동 업로드 안 해도 됨.
    import subprocess
    dst = ("s3://boaz-1234-825494477740-ap-northeast-2-an/"
           "iceberg_warehouse/stage_c_silver/stage3/first_detection.csv")
    r = subprocess.run(["aws", "s3", "cp", OUT, dst, "--region", "ap-northeast-2"],
                       capture_output=True, text=True)
    log(f"S3 업로드 rc={r.returncode} {r.stderr.strip()[-200:]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
