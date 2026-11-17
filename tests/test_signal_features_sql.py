"""contracts/dbt/models/gold/fct_signal_features.sql (D단계 v3~v5 피처) 로직 검증.

S3 Iceberg(수억 행) 대신 함정이 들어간 작은 가짜 데이터를 DuckDB에 넣고, dbt 모델 SQL을 그대로
렌더링해 실행한다(ref/iceberg_scan만 테스트 테이블로 바꿈). 핵심은 두 가지:
  1) 피처를 fct_signal_scores와 **같은 신고 집단**으로 센다 — 두 모델의 (ingredient, pt, a)가 같다
  2) 비율 정의 — 옛 caseversion·병용약(C)·결과 없는 신고·빈 dechal·같은 성분 행 중복을 올바로 처리
"""
import re
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

REPO = Path(__file__).resolve().parents[1]
GOLD = REPO / "contracts" / "dbt" / "models" / "gold"


def render(sql_path: Path) -> str:
    sql = sql_path.read_text(encoding="utf-8")
    sql = re.sub(r"\{\{\s*config\(.*?\)\s*\}\}", "", sql)
    sql = re.sub(r"\{\{\s*ref\('(\w+)'\)\s*\}\}", r"\1", sql)
    sql = re.sub(r"iceberg_scan\('[^']*faers_outc'\)", "outc_src", sql)
    sql = re.sub(r"iceberg_scan\('[^']*faers_drug'\)", "drug_src", sql)
    return sql


# fct_case_drug_reaction 그레인: 신고 1건의 약물 1개 × 반응 1개
CDR = [
    # caseid, primaryid, drug_seq, caseversion, fda_dt, role_cod, drugname, ingredient_set, unii, pt
    # C1 옛 버전(무시돼야 함): aspirin × rash, 결과 DE
    ("C1", "P11", 1, 1, "20190101", "PS", "ASPIRIN", "aspirin", "", "rash"),
    # C1 최신판: aspirin(PS) × headache·nausea, ibuprofen은 병용(C)이라 제외
    ("C1", "P12", 1, 2, "20260301", "PS", "ASPIRIN", "aspirin", "", "headache"),
    ("C1", "P12", 1, 2, "20260301", "PS", "ASPIRIN", "aspirin", "", "nausea"),
    ("C1", "P12", 2, 2, "20260301", "C", "ADVIL", "ibuprofen", "", "headache"),
    ("C1", "P12", 2, 2, "20260301", "C", "ADVIL", "ibuprofen", "", "nausea"),
    # C2: 복합제(SS) × headache, 결과 행 없음, dechal 빈 값, 오래된 접수일
    ("C2", "P21", 1, 1, "20200101", "SS", "EXCEDRIN", "aspirin|caffeine", "", "headache"),
    # C3: 같은 성분 약물 행 2개(dechal Y / N), 결과 OT(중대 아님)
    ("C3", "P31", 1, 1, "20250601", "PS", "ASPIRIN", "aspirin", "", "headache"),
    ("C3", "P31", 2, 1, "20250601", "PS", "BAYER", "aspirin", "", "headache"),
]
OUTC = [("P11", "DE"), ("P12", "DE"), ("P12", "HO"), ("P31", "OT")]
DRUG = [
    # primaryid, drug_seq, dechal, rechal
    ("P11", "1", "Y", "Y"),
    ("P12", "1", "Y", "N"),
    ("P12", "2", "Y", "Y"),   # 병용약 — 피처에 섞이면 안 됨
    ("P21", "1", "", ""),
    ("P31", "1", "Y", ""),
    ("P31", "2", "N", "N"),
]


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect()
    c.execute("""create table fct_case_drug_reaction(caseid varchar, primaryid varchar, drug_seq integer,
                 caseversion integer, fda_dt varchar, role_cod varchar, drugname varchar,
                 ingredient_set varchar, unii varchar, pt varchar)""")
    c.executemany("insert into fct_case_drug_reaction values (?,?,?,?,?,?,?,?,?,?)", CDR)
    c.execute("create table outc_src(primaryid varchar, caseid varchar, outc_cod varchar)")
    c.executemany("insert into outc_src values (?, 'x', ?)", OUTC)
    c.execute("create table drug_src(primaryid varchar, drug_seq varchar, dechal varchar, rechal varchar)")
    c.executemany("insert into drug_src values (?,?,?,?)", DRUG)
    c.execute("create table fct_signal_scores as " + render(GOLD / "fct_signal_scores.sql"))
    c.execute("create table fct_signal_features as " + render(GOLD / "fct_signal_features.sql"))
    c.execute("create table fct_signal_ranking_input as " + render(GOLD / "fct_signal_ranking_input.sql"))
    return c


def rows(con, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def feature(con, ingredient, pt):
    r = rows(con, f"select * from fct_signal_features where ingredient='{ingredient}' and pt='{pt}'")
    assert len(r) == 1, (ingredient, pt, r)
    return r[0]


def test_same_population_as_signal_scores(con):
    scores = {(r["ingredient"], r["pt"], r["a"]) for r in rows(con, "select ingredient, pt, a from fct_signal_scores")}
    feats = {(r["ingredient"], r["pt"], r["a"]) for r in rows(con, "select ingredient, pt, a from fct_signal_features")}
    assert feats == scores
    # 옛 버전(rash)·병용약(ibuprofen)은 둘 다에서 빠진다
    assert not any(pt == "rash" or ing == "ibuprofen" for ing, pt, _ in feats)


def test_aspirin_headache_fractions(con):
    f = feature(con, "aspirin", "headache")       # 신고 C1·C2·C3
    assert f["a"] == 3
    assert f["death_frac"] == pytest.approx(1 / 3)    # C1만 DE (옛 버전 P11의 DE는 무시)
    assert f["serious_frac"] == pytest.approx(1 / 3)  # C1(DE·HO), C3의 OT는 중대 아님, C2는 결과 없음
    assert f["dechal_frac"] == pytest.approx(2 / 3)   # C1 Y, C3 행 중 하나 Y, C2 빈 값
    assert f["rechal_frac"] == pytest.approx(0)       # 병용약(P12 seq2)의 rechal Y가 섞이면 안 됨
    assert f["dechal_known_n"] == 2                   # C1, C3
    assert f["rechal_known_n"] == 2                   # C1(N), C3(N)
    assert f["recency"] == pytest.approx(2 / 3)       # 기준일 2026-03-01 — C1·C3는 2년 안, C2(2020)는 밖


def test_combo_ingredient_and_single_case_pairs(con):
    caffeine = feature(con, "caffeine", "headache")   # 복합제를 성분 단위로 쪼갠 쪽
    assert (caffeine["a"], caffeine["death_frac"], caffeine["dechal_frac"], caffeine["recency"]) == (1, 0, 0, 0)
    nausea = feature(con, "aspirin", "nausea")        # C1만
    assert (nausea["a"], nausea["death_frac"], nausea["serious_frac"], nausea["dechal_frac"]) == (1, 1, 1, 1)


def test_ranking_input_has_scores_and_feature_columns(con):
    r = rows(con, "select * from fct_signal_ranking_input")
    assert len(r) == len(rows(con, "select * from fct_signal_scores"))
    for col in ("ic025", "death_frac", "dechal_frac", "rechal_frac", "recency"):
        assert col in r[0], col
    assert all(x["death_frac"] is not None for x in r)   # 같은 기준으로 세서 빠지는 조합이 없다


def test_dbt_tests_pass_on_fixture(con):
    # 계약 테스트 SQL(위반 행을 돌려주면 실패)을 같은 데이터로 돌려 0행인지 확인
    for name in ("assert_features_align_with_scores.sql", "assert_feature_fractions_in_range.sql"):
        sql = render(REPO / "contracts" / "dbt" / "tests" / name)
        assert con.execute(sql).fetchall() == [], name
