"""v2 시점(PIT): 각 (성분,반응)이 **처음 신호로 뜬 분기**를 뽑는다 (EC2용).

as_of 스냅샷을 수십 번 export 하는 대신, fact 를 한 번만 만들고 연/분기 cutoff 를
루프해서 각 (ingredient, pt) 의 **최초 게이트 통과 분기**(first_q) 하나만 남긴다.
결과는 작은 CSV(first_detection.csv) 라 로컬로 받아 리드타임 계산에 바로 쓴다
(scoring/pit.py). 리드타임 = gold 조치 분기 − first_q.

분기 인덱스는 선형(year*4 + 분기, scoring.pit.quarter_index 와 동일) — 뺄셈이 곧 분기 수.

실행(EC2, 사전·resolution 준비된 상태):
  python3 scripts/build_first_detection.py \
      --raw-map <raw_map.csv> --resolution <resolution.csv> \
      --out ~/first_detection.csv [--granularity year|quarter]

입력은 build_signal_scores.py 와 동일(S3 iceberg + C단계 CSV 2개). 인스턴스 role 로 S3 read.
"""

from __future__ import annotations

import argparse
import os
import time

HOME = os.path.expanduser("~")
S3_DEFAULT = ("s3://boaz-1234-825494477740-ap-northeast-2-an/"
              "iceberg_warehouse/stage_a_raw")
PRODRUN = f"{HOME}/함하경/faers-pipeline/out/prod_run3/final"


def _agg_sql(cutoff: int) -> str:
    """linq <= cutoff 인 신고만으로 게이트(IC025>0 & a>=3) 통과한 (ingredient,pt)."""
    return f"""
    with lr0 as (select * from fact where linq <= {cutoff}),
    lv as (select caseid, max(caseversion) as caseversion from lr0 group by caseid),
    lr as (select f.* from lr0 f
           join lv on f.caseid=lv.caseid and f.caseversion=lv.caseversion),
    cd as (select distinct caseid, trim(ing) as ingredient
           from lr, unnest(string_split(ingredient_set,'|')) as t(ing)
           where role_cod in ('PS','SS') and ingredient_set is not null and ingredient_set<>''),
    cr as (select distinct caseid, pt from lr where pt is not null and pt<>''),
    nt as (select count(distinct caseid) as n from lr),
    dt as (select ingredient, count(distinct caseid) as ab from cd group by ingredient),
    rt as (select pt, count(distinct caseid) as ac from cr group by pt),
    co as (select cd.ingredient, cr.pt, count(distinct cd.caseid) as a
           from cd join cr on cd.caseid=cr.caseid group by 1,2)
    select co.ingredient, co.pt
    from co join dt on co.ingredient=dt.ingredient
            join rt on co.pt=rt.pt cross join nt
    where co.a >= 3
      and log2((co.a+0.5)/((dt.ab*rt.ac*1.0/nt.n)+0.5))
          - 3.3*power(co.a+0.5,-0.5) - 2*power(co.a+0.5,-1.5) > 0
    """


def main() -> int:
    ap = argparse.ArgumentParser(description="PIT 최초 탐지 분기 추출 (v2)")
    ap.add_argument("--s3", default=S3_DEFAULT)
    ap.add_argument("--raw-map", default=f"{PRODRUN}/raw_map.csv")
    ap.add_argument("--resolution", default=f"{PRODRUN}/resolution.csv")
    ap.add_argument("--out", default=f"{HOME}/first_detection.csv")
    ap.add_argument("--db", default=f"{HOME}/d_pit.duckdb")
    ap.add_argument("--tmp", default=f"{HOME}/duckdb_tmp")
    ap.add_argument("--mem", default="12GB")
    ap.add_argument("--granularity", choices=["year", "quarter"], default="year",
                    help="cutoff 격자 (year=연 1점, 빠름 / quarter=분기, 느리지만 리드 정밀)")
    args = ap.parse_args()

    import duckdb
    os.makedirs(args.tmp, exist_ok=True)
    con = duckdb.connect(args.db)
    for s in [f"SET memory_limit='{args.mem}'", f"SET temp_directory='{args.tmp}'",
              "SET preserve_insertion_order=false", "SET unsafe_enable_version_guessing=true",
              "INSTALL httpfs", "LOAD httpfs", "INSTALL iceberg", "LOAD iceberg",
              "CREATE OR REPLACE SECRET faers_s3 (TYPE s3, PROVIDER credential_chain, REGION 'ap-northeast-2')"]:
        con.execute(s)

    t0 = time.time()
    # fact (슬림 + 선형 분기 linq). fda_dt=YYYYMMDD → year*4 + (month-1)//3
    con.execute(f"""
        CREATE OR REPLACE TABLE fact AS
        with drug as (select distinct primaryid, caseid, role_cod, drugname, drug_seq
                      from iceberg_scan('{args.s3}/faers_drug')),
        reac as (select distinct primaryid, pt from iceberg_scan('{args.s3}/faers_reac')),
        demo as (select primaryid, caseversion, fda_dt from iceberg_scan('{args.s3}/faers_demo')),
        em as (select raw_drug_name, entity_id from read_csv_auto('{args.raw_map}', all_varchar=true)),
        res as (select entity_id, resolved_ingredient_set from read_csv_auto('{args.resolution}', all_varchar=true))
        select d.caseid, cast(dm.caseversion as integer) as caseversion, d.role_cod,
               r2.resolved_ingredient_set as ingredient_set, r.pt,
               cast(substr(dm.fda_dt,1,4) as integer)*4
                 + (cast(substr(dm.fda_dt,5,2) as integer)-1)/3 as linq
        from drug d
        join reac r on d.primaryid=r.primaryid
        join demo dm on d.primaryid=dm.primaryid
        left join em m on upper(trim(d.drugname))=m.raw_drug_name
        left join res r2 on m.entity_id=r2.entity_id
        where length(dm.fda_dt) >= 6
    """)
    print(f"fact rows: {con.execute('select count(*) from fact').fetchone()[0]:,} ({time.time()-t0:.0f}s)", flush=True)

    lo, hi = con.execute("select min(linq), max(linq) from fact").fetchone()
    if args.granularity == "year":
        grid = [y * 4 + 3 for y in range(lo // 4, hi // 4 + 1)]  # 각 연도 Q4
    else:
        grid = list(range(int(lo), int(hi) + 1))
    print(f"cutoff 격자 {len(grid)}개: {grid[0]}..{grid[-1]} ({args.granularity})", flush=True)

    con.execute("CREATE OR REPLACE TABLE det(ingredient varchar, pt varchar, cutoff integer)")
    for i, c in enumerate(grid, 1):
        con.execute(f"INSERT INTO det SELECT ingredient, pt, {c} FROM ({_agg_sql(c)})")
        print(f"  [{i}/{len(grid)}] cutoff {c} 완료 ({time.time()-t0:.0f}s)", flush=True)

    con.execute("""CREATE OR REPLACE TABLE first_detection AS
                   select ingredient, pt, min(cutoff) as first_q from det group by 1,2""")
    n = con.execute("select count(*) from first_detection").fetchone()[0]
    con.execute(f"COPY first_detection TO '{args.out}' (HEADER)")
    print(f"first_detection {n:,}행 -> {args.out} ({time.time()-t0:.0f}s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
