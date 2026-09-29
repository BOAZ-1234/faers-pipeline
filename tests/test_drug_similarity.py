"""silver/drug_dict/similarity.py (2단계 글자 유사도) 단위 테스트.

실제 S3·drug_ingredient_map.csv 없이, 합성 사전/miss로 매칭 로직만 검증한다
(tests/CLAUDE.md의 'S3 안 건드림' 원칙과 동일 취지). similarity는 rapidfuzz가
있어야 import되므로, 없으면 skip한다.
"""
import csv
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DRUG_DICT = REPO_ROOT / "silver" / "drug_dict"
if str(DRUG_DICT) not in sys.path:
    sys.path.insert(0, str(DRUG_DICT))

similarity = pytest.importorskip(
    "similarity", reason="rapidfuzz 필요 (pip install rapidfuzz)")


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture
def synthetic_map(tmp_path):
    """build_map.py 출력 스키마의 작은 사전."""
    path = tmp_path / "map.csv"
    _write_csv(
        path,
        ["medicinalproduct", "ingredient_norm", "ingredient_set", "unii",
         "source", "method", "confidence", "dictionary_version"],
        [
            ("ASPIRIN", "ASPIRIN", "aspirin", "", "test", "사전", "", "d-test"),
            ("LISINOPRIL HYDROCHLOROTHIAZIDE", "LISINOPRIL",
             "hydrochlorothiazide|lisinopril", "", "test", "사전", "", "d-test"),
            ("AMOXICILLIN", "AMOXICILLIN", "amoxicillin", "", "test", "사전", "", "d-test"),
            ("METFORMIN", "METFORMIN", "metformin", "", "test", "사전", "", "d-test"),
            # 서로 매우 닮은 두 이름 → 애매성(마진) 검증용
            ("SORAFENIB", "SORAFENIB", "sorafenib", "", "test", "사전", "", "d-test"),
            ("SORANIB", "SORANIB", "soranib", "", "test", "사전", "", "d-test"),
            # 특수문자(하이픈)만 다른 변형 → 짧은이름 예외 검증용
            ("SOLU-CORTEF", "HYDROCORTISONE", "hydrocortisone", "", "test", "사전", "", "d-test"),
        ],
    )
    return path


def _match_map(map_path, tmp_path, miss_rows, **kw):
    """miss를 매칭해 {drugname_raw: 결과행} 반환."""
    miss_path = tmp_path / "miss.csv"
    _write_csv(miss_path, ["name", "n_reports"], miss_rows)
    out_path = tmp_path / "out.csv"
    # short_threshold 기본을 0으로 둬 짧은이름 규칙을 꺼둔다 — 이 헬퍼를 쓰는 기존
    # 테스트들은 오타/용량/마진 로직을 짧은 합성명으로 검증하는 것이라, 규칙이 켜지면
    # 검증 대상이 흐려진다. 규칙 자체는 아래 전용 테스트가 short_threshold를 넘겨 검증.
    params = dict(map_path=map_path, scorer_name="token_sort_ratio",
                  threshold=92.0, limit=5, margin=similarity.DEFAULT_MARGIN,
                  short_threshold=0.0, short_maxlen=10)
    params.update(kw)
    similarity.run_match(miss_path, out_path, params["map_path"], params["scorer_name"],
                         params["threshold"], params["limit"], params["margin"],
                         params["short_threshold"], params["short_maxlen"])
    with open(out_path, encoding="utf-8") as f:
        return {r["drugname_raw"]: r for r in csv.DictReader(f)}


def test_block_key_order_independent():
    """어순이 달라도 같은 블로킹 키(정렬 첫 토큰의 첫 글자)."""
    assert similarity.block_key("LISINOPRIL HYDROCHLOROTHIAZIDE") == \
           similarity.block_key("HYDROCHLOROTHIAZIDE LISINOPRIL")


def test_typo_is_matched(synthetic_map, tmp_path):
    res = _match_map(synthetic_map, tmp_path, [("ASPRIN", 500)], threshold=88.0)
    assert "ASPRIN" in res
    assert res["ASPRIN"]["ingredient_norm"] == "ASPIRIN"
    assert res["ASPRIN"]["method"] == "유사도"


def test_word_order_variation_is_matched(synthetic_map, tmp_path):
    """§4-2가 잡으라고 한 '어순' 케이스 — 블로킹이 놓치면 안 된다."""
    res = _match_map(synthetic_map, tmp_path,
                     [("HYDROCHLOROTHIAZIDE LISINOPRIL", 300)], threshold=90.0)
    assert res["HYDROCHLOROTHIAZIDE LISINOPRIL"]["ingredient_norm"] == "LISINOPRIL"


def test_dosage_form_stripped_then_matched(synthetic_map, tmp_path):
    """normalize_query가 용량('500MG')을 떼고, 남은 오타를 유사도가 흡수."""
    res = _match_map(synthetic_map, tmp_path, [("AMOXICILIN 500MG", 200)], threshold=88.0)
    assert res["AMOXICILIN 500MG"]["ingredient_norm"] == "AMOXICILLIN"
    assert res["AMOXICILIN 500MG"]["normalized"] == "AMOXICILIN"


def test_unrelated_name_not_matched(synthetic_map, tmp_path):
    """엉뚱한 이름은 채택 안 됨(precision 우선 — 오매칭이 집계를 오염시킴)."""
    res = _match_map(synthetic_map, tmp_path, [("ZZZQ NONEXISTENT DRUG", 10)])
    assert "ZZZQ NONEXISTENT DRUG" not in res


def test_ambiguous_pair_held_by_margin(synthetic_map, tmp_path):
    """1·2등 점수가 초박빙이면(SORAFENIB vs SORANIB) 채택하지 않고 3단계로 넘긴다."""
    res = _match_map(synthetic_map, tmp_path, [("SORAFNIB", 20)],
                     threshold=88.0, margin=3.0)
    assert "SORAFNIB" not in res
    # 마진을 0으로 풀면 채택되어야(로직상 임계값은 넘김) — 보류가 마진 때문임을 확인
    res2 = _match_map(synthetic_map, tmp_path, [("SORAFNIB", 20)],
                      threshold=88.0, margin=0.0)
    assert "SORAFNIB" in res2


def test_short_name_needs_higher_threshold(synthetic_map, tmp_path):
    """짧은 단일토큰 이름은 --short-threshold를 넘어야 채택된다.
    'ASPRIN'↔'ASPIRIN'은 한 글자 차라 점수가 ~92 — 긴 이름이면 채택될 점수지만,
    짧은 이름에선 오매칭('INSULIN'↔'INULIN'류) 위험이 커서 벽을 높인다.
    (short_threshold=96이면 걸러지고, 규칙을 풀면=88 채택되어야 함 → 탈락 원인이
    바로 짧은이름 규칙임을 확인.)"""
    blocked = _match_map(synthetic_map, tmp_path, [("ASPRIN", 500)],
                         threshold=88.0, short_threshold=96.0)
    assert "ASPRIN" not in blocked

    allowed = _match_map(synthetic_map, tmp_path, [("ASPRIN", 500)],
                         threshold=88.0, short_threshold=88.0)
    assert allowed["ASPRIN"]["ingredient_norm"] == "ASPIRIN"


def test_punctuation_variant_bypasses_short_rule(synthetic_map, tmp_path):
    """짧은 단일토큰이라도 특수문자(하이픈·물음표 등)만 다르고 글자가 완전히 같으면
    ('SOLUCORTEF'↔'SOLU-CORTEF') 오매칭일 수 없으므로 --short-threshold를 면제하고
    기본 임계값만 본다 — 즉 short_threshold=96에서도 채택되어야 한다."""
    res = _match_map(synthetic_map, tmp_path, [("SOLUCORTEF", 200)],
                     threshold=92.0, short_threshold=96.0)
    assert res["SOLUCORTEF"]["ingredient_norm"] == "HYDROCORTISONE"


def test_output_carries_dictionary_version(synthetic_map, tmp_path):
    """출력은 실제 매칭에 쓴 사전 행의 dictionary_version을 물려받는다(재현성)."""
    res = _match_map(synthetic_map, tmp_path, [("ASPRIN", 500)], threshold=88.0)
    assert res["ASPRIN"]["dictionary_version"] == "d-test"
