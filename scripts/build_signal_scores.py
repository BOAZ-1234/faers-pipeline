"""fct_signal_scores(= D 백테스트 입력)를 dbt 없이 duckdb로 직접 뽑는다.

dbt(contracts/dbt)가 설치 안 된 박스에서도 돌게, b_stage/fct_case_drug_reaction.sql +
gold/fct_signal_scores.sql 의 로직을 그대로 옮겨 S3 Iceberg(A단계 원본)를 직접 집계한다.
dbt도 내부적으론 duckdb로 같은 SQL을 돌리므로 결과는 동일하다.

입력(C단계 prod_run3 본실행 산출물 — 원본은 함하경 out/prod_run3/final):
  --raw-map       raw_drug_name,entity_id          (dbt의 raw_name_entity_map.csv)
  --resolution    entity_id,resolved_ingredient_set (dbt의 resolution_merged.csv)
기본 입력 경로는 $D_PRODRUN (기본값 ~/prod_run3/final) — 개인 홈 경로를 박지 않는다.
  export D_PRODRUN=~/prod_run3/final   # 또는 --raw-map/--resolution 로 직접 지정
출력: ingredient,pt,a,ic025 CSV (scoring.backtest --signals 입력)

실행:
  python3 scripts/build_signal_scores.py            # 기본 경로로
  nohup python3 scripts/build_signal_scores.py > ~/d_build.log 2>&1 &   # 백그라운드

인스턴스 role(faers-ec2-role)로 S3 read 되므로 AWS 자격증명 설정 불필요.
S3 617M행 스캔이라 수~십수 분 + 디스크 spill. 메모리 터지면 --mem 낮춰라.
"""

from __future__ import annotations

import argparse
import os
import time

HOME = os.path.expanduser("~")
S3_DEFAULT = ("s3://boaz-1234-825494477740-ap-northeast-2-an/"
              "iceberg_warehouse/stage_a_raw")
# 개인 홈 경로를 하드코딩하지 않는다 — $D_PRODRUN 로 덮거나 --raw-map/--resolution 지정.
PRODRUN = os.environ.get("D_PRODRUN", f"{HOME}/prod_run3/final")


def main() -> int:
    ap = argparse.ArgumentParser(description="fct_signal_scores 직접 빌드 (duckdb)")
    ap.add_argument("--s3", default=S3_DEFAULT, help="stage_a_raw iceberg 루트")
    ap.add_argument("--raw-map", default=f"{PRODRUN}/raw_map.csv")
    ap.add_argument("--resolution", default=f"{PRODRUN}/resolution.csv")
    ap.add_argument("--out", default=f"{HOME}/fct_signal_scores.csv")
    ap.add_argument("--db", default=f"{HOME}/d_build.duckdb")
    ap.add_argument("--tmp", default=f"{HOME}/duckdb_tmp")
    ap.add_argument("--mem", default="12GB", help="duckdb memory_limit")
    ap.add_argument("--threads", type=int, default=0, help="0=기본")
    ap.add_argument("--as-of", default=None,
                    help="시점(PIT) 집계: fda_dt < YYYYMMDD 인 신고만 집계 (v2). "
                         "없으면 전 기간. 출력파일명에 _asof<날짜> 붙음")
    args = ap.parse_args()

    import duckdb  # 없으면 여기서 ImportError → pip install duckdb

    os.makedirs(args.tmp, exist_ok=True)
    con = duckdb.connect(args.db)
    setup = [
        f"SET memory_limit='{args.mem}'",
        f"SET temp_directory='{args.tmp}'",
        "SET preserve_insertion_order=false",
        "SET unsafe_enable_version_guessing=true",
        "INSTALL httpfs", "LOAD httpfs", "INSTALL iceberg", "LOAD iceberg",
        "CREATE OR REPLACE SECRET faers_s3 "
        "(TYPE s3, PROVIDER credential_chain, REGION 'ap-northeast-2')",
    ]
    if args.threads:
        setup.insert(0, f"SET threads={args.threads}")
    for s in setup:
        con.execute(s)

    # CSV 컬럼 확인 (이름 다르면 바로 알 수 있게)
    for label, path in (("raw_map", args.raw_map), ("resolution", args.resolution)):
        cols = [c[0] for c in con.execute(
            f"DESCRIBE SELECT * FROM read_csv_auto('{path}', all_varchar=true)"
        ).fetchall()]
        print(f"{label} cols: {cols}", flush=True)

    t0 = time.time()

    # v2 시점(PIT) 필터: fda_dt < as_of 인 신고만 (조치 이전 데이터로만 집계)
    asof_where = f"\n        where dm.fda_dt < '{args.as_of}'" if args.as_of else ""
    out = args.out
    if args.as_of:
        base, ext = os.path.splitext(args.out)
        out = f"{base}_asof{args.as_of}{ext}"

    # ── fct_case_drug_reaction (SILVER-2 펼치기) — 필요한 컬럼만 슬림하게 ──
    # gold가 쓰는 건 caseid, caseversion, role_cod, ingredient_set, pt 뿐이라 그것만.
    con.execute(f"""
        CREATE OR REPLACE TABLE fact AS
        with drug as (
            select distinct primaryid, caseid, role_cod, drugname, drug_seq
            from iceberg_scan('{args.s3}/faers_drug')
        ),
        reac as (select distinct primaryid, pt from iceberg_scan('{args.s3}/faers_reac')),
        demo as (select primaryid, caseversion, fda_dt from iceberg_scan('{args.s3}/faers_demo')),
        em as (select raw_drug_name, entity_id
               from read_csv_auto('{args.raw_map}', all_varchar=true)),
        res as (select entity_id, resolved_ingredient_set
                from read_csv_auto('{args.resolution}', all_varchar=true))
        select
            d.caseid,
            cast(dm.caseversion as integer) as caseversion,
            d.role_cod,
            r2.resolved_ingredient_set     as ingredient_set,
            r.pt
        from drug d
        join reac r  on d.primaryid = r.primaryid
        join demo dm on d.primaryid = dm.primaryid
        left join em  m  on upper(trim(d.drugname)) = m.raw_drug_name
        left join res r2 on m.entity_id = r2.entity_id{asof_where}
    """)
    print(f"fact rows: {con.execute('select count(*) from fact').fetchone()[0]:,} "
          f"({time.time()-t0:.0f}s)", flush=True)

    # ── gold/fct_signal_scores — 2×2표 + IC025 (ror/prr/solo·combo 생략, v1엔 불필요) ──
    con.execute("""
        CREATE OR REPLACE TABLE fss AS
        with lv as (select caseid, max(caseversion) as caseversion from fact group by caseid),
        lr as (
            select f.* from fact f
            join lv on f.caseid = lv.caseid and f.caseversion = lv.caseversion
        ),
        cd as (
            select distinct caseid, trim(ing) as ingredient
            from lr, unnest(string_split(ingredient_set, '|')) as t(ing)
            where role_cod in ('PS','SS')
              and ingredient_set is not null and ingredient_set <> ''
        ),
        cr as (select distinct caseid, pt from lr where pt is not null and pt <> ''),
        nt as (select count(distinct caseid) as n from lr),
        dt as (select ingredient, count(distinct caseid) as a_plus_b from cd group by ingredient),
        rt as (select pt, count(distinct caseid) as a_plus_c from cr group by pt),
        co as (
            select cd.ingredient, cr.pt, count(distinct cd.caseid) as a
            from cd join cr on cd.caseid = cr.caseid
            group by cd.ingredient, cr.pt
        )
        select
            co.ingredient, co.pt, co.a, dt.a_plus_b, rt.a_plus_c, nt.n,
            log2((co.a + 0.5) / ((dt.a_plus_b * rt.a_plus_c * 1.0 / nt.n) + 0.5))
              - 3.3 * power(co.a + 0.5, -0.5)
              - 2   * power(co.a + 0.5, -1.5) as ic025
        from co
        join dt on co.ingredient = dt.ingredient
        join rt on co.pt = rt.pt
        cross join nt
    """)
    print(f"signal rows: {con.execute('select count(*) from fss').fetchone()[0]:,} "
          f"({time.time()-t0:.0f}s)", flush=True)

    con.execute(
        f"COPY (SELECT ingredient, pt, a, ic025 FROM fss) TO '{out}' (HEADER)"
    )
    print(f"exported -> {out}" + (f"  (as_of {args.as_of})" if args.as_of else ""),
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
