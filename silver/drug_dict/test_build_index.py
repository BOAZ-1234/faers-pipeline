"""build_index.py 핵심 로직 self-check — 프레임워크 없이 assert만."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from build_index import extract_aliases, MAP_DEFAULT


def _mini_map(tmp_path: Path, rows: list[dict]) -> Path:
    import csv
    p = tmp_path / "mini_map.csv"
    fields = ["medicinalproduct", "ingredient_norm", "ingredient_set",
              "unii", "source", "method", "confidence", "dictionary_version"]
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})
    return p


def test_alias_extraction():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = _mini_map(Path(td), [
            {
                "medicinalproduct": "ASPIRIN 81 MG TABLET",  # normalize_query가 잘 떼는 케이스
                "ingredient_norm": "ASPIRIN",
                "ingredient_set": "aspirin",
            },
        ])
        rows = extract_aliases(p)
    aliases = {r["alias"] for r in rows}
    # 원본·정규화본(ASPIRIN)·성분명(ASPIRIN) — 정규화본=성분명이라 원본+성분명 = 2개 이상
    assert len(aliases) >= 2, f"alias 부족: {aliases}"
    assert "ASPIRIN 81 MG TABLET" in aliases
    assert "ASPIRIN" in aliases, f"normalize_query 결과 없음: {aliases}"


def test_no_duplicate_alias():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = _mini_map(Path(td), [
            {"medicinalproduct": "ASPIRIN", "ingredient_norm": "ASPIRIN",
             "ingredient_set": "aspirin"},
            {"medicinalproduct": "ASPIRIN 81MG", "ingredient_norm": "ASPIRIN",
             "ingredient_set": "aspirin"},
        ])
        rows = extract_aliases(p)
    aliases = [r["alias"] for r in rows]
    assert len(aliases) == len(set(aliases)), "중복 alias 존재"


def test_ingredient_set_preserved():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = _mini_map(Path(td), [
            {"medicinalproduct": "TRUVADA",
             "ingredient_norm": "EMTRICITABINE",
             "ingredient_set": "emtricitabine|tenofovir disoproxil fumarate"},
        ])
        rows = extract_aliases(p)
    by_alias = {r["alias"]: r["ingredient_set"] for r in rows}
    # 브랜드는 복합제 묶음 그대로
    assert by_alias["TRUVADA"] == "emtricitabine|tenofovir disoproxil fumarate"
    # 복합제 성분 별칭은 그 성분 하나로 접힘 (#47 5,665개 버그 수정)
    assert by_alias["EMTRICITABINE"] == "emtricitabine", f"collapse 실패: {by_alias}"


def test_collapse_single_ingredient_unchanged():
    """단일제는 묶음이 1개라 collapse 대상 아님 — 그대로 유지."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = _mini_map(Path(td), [
            {"medicinalproduct": "ASPIRIN 81MG", "ingredient_norm": "ASPIRIN",
             "ingredient_set": "aspirin"},
        ])
        rows = extract_aliases(p)
    for r in rows:
        assert r["ingredient_set"] == "aspirin"


def test_normalize_import():
    from normalize import normalize_query
    assert normalize_query("ASPIRIN 81 MG TABLET") == "ASPIRIN"
    assert normalize_query("LISINOPRIL TABLETS USP, 20MG") == "LISINOPRIL"


def test_index_roundtrip():
    """10행 미니 corpus로 인덱스 빌드 → 동일 alias 쿼리 → Top-1 정답."""
    try:
        import faiss
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("  skip test_index_roundtrip: faiss/sentence-transformers 미설치")
        return

    import tempfile
    from build_index import embed, PASSAGE_PREFIX, QUERY_PREFIX

    model = SentenceTransformer("intfloat/e5-small-v2")

    aliases = ["ASPIRIN", "IBUPROFEN", "ACETAMINOPHEN",
               "LISINOPRIL", "ATORVASTATIN", "METFORMIN",
               "OMEPRAZOLE", "SERTRALINE", "GABAPENTIN", "PREDNISONE"]
    isets = [a.lower() for a in aliases]

    vecs = embed(aliases, model, PASSAGE_PREFIX["e5"])
    index = faiss.IndexFlatIP(vecs.shape[1])
    index.add(vecs)

    q_vecs = embed(aliases[:3], model, QUERY_PREFIX["e5"])
    _, idxs = index.search(q_vecs, 1)
    for i in range(3):
        assert idxs[i][0] == i, f"Top-1 mismatch at {aliases[i]}: got {idxs[i][0]}"


if __name__ == "__main__":
    test_alias_extraction()
    test_no_duplicate_alias()
    test_ingredient_set_preserved()
    test_collapse_single_ingredient_unchanged()
    test_normalize_import()
    test_index_roundtrip()
    print("모든 체크 통과")
