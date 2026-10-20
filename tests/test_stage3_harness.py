"""silver/drug_dict/stage3_harness.py (3단계 하네스 뼈대) 단위 테스트.

배관이 맞는지만 본다 — 정확도는 하네스 완성 후 DiAna dev로 잰다. 네트워크·S3·GPU 없이
가짜 검색(FixtureSource)과 가짜 판정기로 돌린다.
"""
import csv
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DRUG_DICT = REPO_ROOT / "silver" / "drug_dict"
if str(DRUG_DICT) not in sys.path:
    sys.path.insert(0, str(DRUG_DICT))

import stage3_harness as h  # noqa: E402

ALLOWED_STATUS = {"확정", "보류", "미해결", "수기"}  # schema.yml accepted_values

# 손으로 만든 10줄 샘플 (설계 '레인 ②: 10줄짜리 샘플로 spine부터')
SAMPLE_ROWS = [
    ("KLACID", 120),
    ("KLACID 500 MG", 30),            # KLACID와 같은 entity로 묶여야 함
    ("ZOMETA", 80),
    ("SYMBYAX", 50),                  # 복합제 → MULTI
    ("XYZFAKEDRUG", 40),              # 후보 없음 → LLM 미호출
    ("SINEMET", 35),
    ("CLARYTHROMYCIN", 20),
    ("ADVIL", 15),
    ("PLACEBO", 10),
    ("HUMIRA", 5),
]

FIXTURE = {
    "KLACID": [{"alias": "CLARITHROMYCIN", "ingredient_set": "clarithromycin", "unii": "H1250JIK0A",
                "semantic_score": 0.71},
               {"alias": "AZITHROMYCIN", "ingredient_set": "azithromycin", "semantic_score": 0.65}],
    "ZOMETA": [{"alias": "ZOMETA", "ingredient_set": "zoledronic acid", "unii": "6XC1PAD3KF",
                "semantic_score": 0.99}],
    "SYMBYAX": [{"alias": "OLANZAPINE", "ingredient_set": "olanzapine", "semantic_score": 0.6},
                {"alias": "FLUOXETINE", "ingredient_set": "fluoxetine", "semantic_score": 0.55}],
    "SINEMET": [{"alias": "SINEMET", "ingredient_set": "carbidopa|levodopa", "semantic_score": 0.98}],
    "CLARYTHROMYCIN": [{"alias": "CLARITHROMYCIN", "ingredient_set": "clarithromycin", "lexical_score": 93.0}],
    "ADVIL": [{"alias": "ADVIL", "ingredient_set": "ibuprofen", "semantic_score": 0.97}],
    "PLACEBO": [{"alias": "PLACEBO", "ingredient_set": "", "semantic_score": 0.9}],  # 성분 없는 후보는 버림
    "HUMIRA": [{"alias": "HUMIRA", "ingredient_set": "adalimumab", "semantic_score": 0.99}],
}


class ScriptedJudge:
    """정규화명별로 정해진 JSON 응답을 돌려주는 판정기 — LLM 응답 파싱 경로까지 태운다."""
    model = "scripted"
    prompt_version = "test-v1"

    def __init__(self, responses: dict, fail_after: int | None = None):
        self.responses = responses
        self.calls = 0
        self.fail_after = fail_after

    def judge(self, entity, cands):
        if self.fail_after is not None and self.calls >= self.fail_after:
            raise RuntimeError("spot 중단 흉내")
        self.calls += 1
        text = self.responses.get(entity.normalized, '{"decision":"NONE","candidate_ids":[],"note":"x"}')
        return h.parse_decision(text, len(cands))


RESPONSES = {
    "KLACID": '{"decision":"MATCH","candidate_ids":[1],"note":"Klacid is clarithromycin"}',
    "ZOMETA": '```json\n{"decision":"MATCH","candidate_ids":[1],"note":"brand"}\n```',
    "SYMBYAX": '{"decision":"MULTI","candidate_ids":[2,1],"note":"olanzapine+fluoxetine"}',
    "SINEMET": '{"decision":"MATCH","candidate_ids":[1],"note":"brand"}',
    "CLARYTHROMYCIN": '{"decision":"MATCH","candidate_ids":[3],"note":"bad id"}',  # 범위 밖 → NONE
    "ADVIL": "ibuprofen",                                                           # JSON 아님 → NONE
}


@pytest.fixture
def fixture_source(tmp_path):
    p = tmp_path / "cands.json"
    p.write_text(json.dumps(FIXTURE), encoding="utf-8")
    return h.FixtureSource(p)


def _entities():
    return h.group_entities(SAMPLE_ROWS)


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── 묶기·합치기·조립 ───────────────────────────────────────────────────────────

def test_group_entities_merges_by_normalized_name():
    ents = {e.normalized: e for e in _entities()}
    klacid = ents["KLACID"]
    assert [n for n, _ in klacid.raw_names] == ["KLACID", "KLACID 500 MG"]
    assert klacid.n_reports == 150
    assert klacid.entity_id == h.entity_id_for("KLACID")  # 결정적 ID
    assert len(ents) == 9
    assert _entities()[0].normalized == "KLACID"  # 신고 많은 순


def test_union_collapses_same_ingredient_set_and_keeps_best_scores():
    a = [h.Candidate("CLARITHROMYCIN", "clarithromycin", semantic_score=0.7, source="faiss")]
    b = [h.Candidate("CLARITH", "clarithromycin", lexical_score=93.0, source="fuzzy"),
         h.Candidate("AZITHROMYCIN", "azithromycin", lexical_score=80.0, source="fuzzy")]
    out = h.union_candidates([a, b], top_k=10)
    assert [c.ingredient_set for c in out] == ["clarithromycin", "azithromycin"]
    assert out[0].semantic_score == 0.7 and out[0].lexical_score == 93.0
    assert out[0].source == "faiss+fuzzy"
    assert len(h.union_candidates([a, b], top_k=1)) == 1


def test_assemble_ingredient_set_sorted_lower():
    cands = [h.Candidate("X", "Levodopa|carbidopa"), h.Candidate("Y", "entacapone")]
    assert h.assemble_ingredient_set(cands) == "carbidopa|entacapone|levodopa"


# ── 응답 검증 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,n,decision,ids", [
    ('{"decision":"MATCH","candidate_ids":[2],"note":""}', 3, "MATCH", [2]),
    ('{"decision":"multi","candidate_ids":[3,1,1],"note":""}', 3, "MULTI", [1, 3]),
    ('{"decision":"NONE","candidate_ids":[],"note":""}', 3, "NONE", []),
    ('noise {"decision":"MATCH","candidate_ids":[1]} noise', 1, "MATCH", [1]),
])
def test_parse_decision_valid(text, n, decision, ids):
    d = h.parse_decision(text, n)
    assert (d.decision, d.candidate_ids) == (decision, ids)


@pytest.mark.parametrize("text,n", [
    ("CLARITHROMYCIN", 3),                                          # JSON 아님
    ('{"decision":"MATCH","candidate_ids":[4]}', 3),                # 범위 밖
    ('{"decision":"MATCH","candidate_ids":[1,2]}', 3),              # MATCH인데 2개
    ('{"decision":"MULTI","candidate_ids":[1]}', 3),                # MULTI인데 1개
    ('{"decision":"NONE","candidate_ids":[1]}', 3),                 # NONE인데 id
    ('{"decision":"MAYBE","candidate_ids":[]}', 3),                 # 모르는 판정
    ('{"decision":"MATCH","candidate_ids":["1"]}', 3),              # 문자열 id
    ('{"decision":"MATCH","candidate_ids":[true]}', 3),             # bool은 int로 안 침
])
def test_parse_decision_invalid_becomes_none(text, n):
    d = h.parse_decision(text, n)
    assert d.decision == "NONE" and d.candidate_ids == [] and d.note.startswith("invalid")


# ── run → finalize ────────────────────────────────────────────────────────────

def test_run_finalize_outputs_follow_contract(tmp_path, fixture_source):
    storage = h.LocalStorage(str(tmp_path / "run"))
    judge = ScriptedJudge(RESPONSES)
    stats = h.run(_entities(), [fixture_source], judge, storage, chunk_size=3,
                  retrieval_version="fixture", log=lambda *_: None)
    assert stats["processed"] == 9
    assert judge.calls == 7  # XYZFAKEDRUG·PLACEBO(성분 없는 후보만)는 LLM 미호출
    assert stats["llm_calls"] == 7

    counts = h.finalize(storage, tmp_path / "final")
    res = {r["raw_drug_name"]: r for r in _read_csv(tmp_path / "final" / "resolution.csv")}
    cand = _read_csv(tmp_path / "final" / "candidate.csv")
    raw_map = _read_csv(tmp_path / "final" / "raw_map.csv")
    assert counts == {"resolution": 9, "candidate": len(cand), "raw_map": 10}

    # 컬럼이 dbt contract와 같은 이름·순서
    with open(tmp_path / "final" / "resolution.csv", encoding="utf-8") as f:
        assert f.readline().strip().split(",") == h.RESOLUTION_FIELDS
    with open(tmp_path / "final" / "candidate.csv", encoding="utf-8") as f:
        assert f.readline().strip().split(",") == h.CANDIDATE_FIELDS

    # 판정 → 상태·성분
    assert res["KLACID"]["resolved_ingredient_set"] == "clarithromycin"
    assert res["KLACID"]["unii"] == "H1250JIK0A"
    assert res["KLACID"]["resolution_status"] == "보류"            # v1: 자동 확정 안 함
    assert res["SYMBYAX"]["resolved_ingredient_set"] == "fluoxetine|olanzapine"  # 코드가 조립
    assert res["SYMBYAX"]["unii"] == ""                              # 묶음은 UNII 없음
    assert res["SINEMET"]["resolved_ingredient_set"] == "carbidopa|levodopa"
    for name in ("CLARYTHROMYCIN", "ADVIL", "XYZFAKEDRUG", "PLACEBO"):
        assert res[name]["resolution_status"] == "미해결"
        assert res[name]["resolved_ingredient_set"] == ""
    assert res["XYZFAKEDRUG"]["resolved_method"] == "none"

    # contract 제약: 상태값 4종, entity_id unique, 후보는 모두 resolution에 존재(relationships)
    assert {r["resolution_status"] for r in res.values()} <= ALLOWED_STATUS
    ids = [r["entity_id"] for r in res.values()]
    assert len(ids) == len(set(ids))
    assert {c["entity_id"] for c in cand} <= set(ids)
    assert all(r["resolved_stage"] == "3" for r in res.values())
    # 후보 순위는 1부터
    klacid_cands = [c for c in cand if c["raw_drug_name"] == "KLACID"]
    assert [c["candidate_rank"] for c in klacid_cands] == ["1", "2"]
    # raw 이름은 raw_map으로 전부 추적
    assert {r["raw_drug_name"] for r in raw_map} == {n for n, _ in SAMPLE_ROWS}


def test_resume_after_interruption_does_not_redo_or_duplicate(tmp_path, fixture_source):
    """판정기가 중간에 죽어도 이미 저장된 chunk는 남고, 재실행하면 나머지만 처리한다."""
    storage = h.LocalStorage(str(tmp_path / "run"))
    dying = ScriptedJudge(RESPONSES, fail_after=3)
    with pytest.raises(RuntimeError):
        h.run(_entities(), [fixture_source], dying, storage, chunk_size=1, log=lambda *_: None)
    saved = h.done_entity_ids(storage)
    # chunk_size=1 → 죽기 직전까지 1건씩 저장됨: LLM 판정 3건 + 그 사이 후보 없어 LLM 안 부른 건
    assert dying.calls == 3
    assert 3 <= len(saved) < 9

    judge = ScriptedJudge(RESPONSES)
    stats = h.run(_entities(), [fixture_source], judge, storage, chunk_size=1, log=lambda *_: None)
    assert stats["processed"] == 9 - len(saved)

    stats2 = h.run(_entities(), [fixture_source], ScriptedJudge(RESPONSES), storage, log=lambda *_: None)
    assert stats2["processed"] == 0  # 다 끝났으면 아무것도 안 함

    counts = h.finalize(storage, tmp_path / "final")
    assert counts["resolution"] == 9


def test_routing_reason_is_entry_reason_not_decision(tmp_path, fixture_source):
    """routing_reason은 '왜 3단계까지 왔나'라 판정 결과와 별개로 기록된다(PR#52 리뷰)."""
    storage = h.LocalStorage(str(tmp_path / "run"))
    h.run(_entities(), [fixture_source], ScriptedJudge(RESPONSES), storage, log=lambda *_: None)
    recs = {r["normalized"]: r for r in h.iter_records(storage)}
    assert (recs["KLACID"]["routing_reason"], recs["KLACID"]["decision"]) == (h.ROUTING_NO_MATCH, "MATCH")
    assert (recs["ADVIL"]["routing_reason"], recs["ADVIL"]["decision"]) == (h.ROUTING_NO_MATCH, "NONE")
    assert recs["XYZFAKEDRUG"]["routing_reason"] == h.ROUTING_NO_CANDIDATES


def test_mock_judge_and_no_source(tmp_path, fixture_source):
    storage = h.LocalStorage(str(tmp_path / "run"))
    stats = h.run(_entities(), [fixture_source], h.MockJudge(), storage, log=lambda *_: None)
    assert stats["MATCH"] >= 3  # alias == 정규화명인 ZOMETA·SINEMET·ADVIL·HUMIRA
    storage2 = h.LocalStorage(str(tmp_path / "run2"))
    stats2 = h.run(_entities(), [h.NoSource()], h.MockJudge(), storage2, log=lambda *_: None)
    assert stats2 == {"processed": 9, "llm_calls": 0, "MATCH": 0, "MULTI": 0, "NONE": 9}


def test_openai_compat_judge_builds_request_and_parses(tmp_path, monkeypatch):
    prompt = tmp_path / "judge_vX.txt"
    prompt.write_text("SYSTEM PROMPT", encoding="utf-8")
    sent = {}

    def fake_post(url, payload, timeout, api_key):
        sent.update(url=url, payload=payload)
        return {"choices": [{"message": {"content": '{"decision":"MATCH","candidate_ids":[1],"note":"ok"}'}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 9}}

    monkeypatch.setattr(h, "_post_json", fake_post)
    judge = h.OpenAICompatJudge("http://gpu:8000/v1/", "qwen-7b", prompt)
    ent = h.group_entities([("KLACID", 1)])[0]
    d = judge.judge(ent, [h.Candidate("CLARITHROMYCIN", "clarithromycin", unii="H1250JIK0A")])

    assert sent["url"] == "http://gpu:8000/v1/chat/completions"
    assert sent["payload"]["model"] == "qwen-7b"
    assert sent["payload"]["messages"][0] == {"role": "system", "content": "SYSTEM PROMPT"}
    assert "1. alias=CLARITHROMYCIN | ingredients=clarithromycin | unii=H1250JIK0A" in \
        sent["payload"]["messages"][1]["content"]
    assert judge.prompt_version == "judge_vX"
    assert (d.decision, d.candidate_ids, d.usage) == ("MATCH", [1], {"input": 50, "output": 9})


def test_cli_run_and_finalize(tmp_path):
    inp = tmp_path / "unknown.csv"
    with open(inp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "n_reports"])
        w.writerows(SAMPLE_ROWS)
    cands = tmp_path / "cands.json"
    cands.write_text(json.dumps(FIXTURE), encoding="utf-8")
    out = tmp_path / "run"
    h.main(["run", str(inp), "--out", str(out), "--fixture-candidates", str(cands),
            "--min-reports", "20", "--limit", "4"])
    h.main(["finalize", "--out", str(out), "--dest", str(tmp_path / "final")])
    rows = _read_csv(tmp_path / "final" / "resolution.csv")
    assert len(rows) == 4  # 신고 ≥20 → 6개 entity 중 앞 4개
    assert rows[0]["pipeline_version"] == h.PIPELINE_VERSION


# ── 실제 후보 소스 (#59) ───────────────────────────────────────────────────────

@pytest.mark.parametrize("alias,iset,expected", [
    # 성분명 별칭이 복합제 묶음을 달고 있으면 그 성분 하나로 (#47 corpus·#43과 같은 문제)
    ("AMLODIPINE", "amlodipine|hydrochlorothiazide|olmesartan medoxomil", "amlodipine"),
    ("OXYCODONE HYDROCHLORIDE", "acetaminophen|oxycodone", "oxycodone"),   # 염 접미사 떼고 원소면 성분
    ("SYMBICORT", "budesonide|formoterol", "budesonide|formoterol"),       # 브랜드는 묶음 유지
    ("ZOMETA", "zoledronic acid", "zoledronic acid"),                      # 단일제는 그대로
])
def test_collapse_ingredient_alias(alias, iset, expected):
    assert h.collapse_ingredient_alias(alias, iset) == expected


class _FakeModel:
    def __init__(self):
        self.calls = []

    def encode(self, texts, **kw):
        self.calls.append(list(texts))
        return [[1.0] for _ in texts]


class _FakeIndex:
    """검색 결과를 미리 정해 둔 FAISS 대역. 행 = 질의, 열 = search_k."""
    def __init__(self, ntotal, ids, scores):
        self.ntotal, self._ids, self._scores = ntotal, ids, scores

    def search(self, vecs, k):
        return [self._scores] * len(vecs), [self._ids] * len(vecs)


CORPUS = [{"alias": "AMLODIPINE", "ingredient_set": "amlodipine|hydrochlorothiazide|olmesartan medoxomil"},
          {"alias": "NORVASC", "ingredient_set": "amlodipine"},
          {"alias": "SYMBICORT", "ingredient_set": "budesonide|formoterol"}]
META = {"dictionary_version": h.DICTIONARY_VERSION, "retrieval_version": "r-test-v1",
        "embedding_model": "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"}


def test_faiss_source_maps_hits_collapses_and_skips_padding():
    idx = _FakeIndex(3, ids=[0, 1, 2, -1], scores=[0.9, 0.8, 0.5, 0.0])  # -1 = FAISS 패딩
    src = h.FaissSource(idx, CORPUS, META, _FakeModel(), search_k=4)
    cands = src.candidates("AMLODIPNE")
    assert [c.alias for c in cands] == ["AMLODIPINE", "NORVASC", "SYMBICORT"]
    assert cands[0].ingredient_set == "amlodipine"            # 복합제 묶음 → 성분 하나
    assert cands[0].semantic_score == 0.9 and cands[0].source == "faiss"
    assert src.retrieval_version == "r-test-v1" and src.prefix == ""
    # 합치면 NORVASC와 같은 성분이라 하나로 접힌다
    assert [c.ingredient_set for c in h.union_candidates([cands], 10)] == ["amlodipine", "budesonide|formoterol"]


def test_faiss_source_prefetch_batches_and_e5_prefix():
    model = _FakeModel()
    meta = dict(META, embedding_model="intfloat/multilingual-e5-small")
    src = h.FaissSource(_FakeIndex(3, [1], [0.7]), CORPUS, meta, model, search_k=1)
    src.prefetch(["A", "B", "A"])
    assert model.calls == [["query: A", "query: B"]]          # 중복 제거·한 번에 임베딩, e5는 query: 접두
    src.candidates("A")
    src.candidates("B")
    assert len(model.calls) == 1                               # 캐시에서 꺼냄
    src.candidates("C")
    assert len(model.calls) == 2                               # 캐시에 없으면 그때 임베딩


@pytest.mark.parametrize("meta,ntotal,msg", [
    (dict(META, dictionary_version="d-2025Q1"), 3, "사전 버전 불일치"),
    (META, 4, "크기가 다름"),
])
def test_faiss_source_rejects_mismatched_corpus(meta, ntotal, msg):
    with pytest.raises(ValueError, match=msg):
        h.FaissSource(_FakeIndex(ntotal, [0], [1.0]), CORPUS, meta, _FakeModel())


def test_run_prefetches_once_per_chunk(tmp_path):
    class CountingSource:
        name = "counting"

        def __init__(self):
            self.prefetched = []

        def prefetch(self, names):
            self.prefetched.append(len(names))

        def candidates(self, normalized):
            return []

    src = CountingSource()
    h.run(_entities(), [src], h.MockJudge(), h.LocalStorage(str(tmp_path / "run")), chunk_size=4,
          log=lambda *_: None)
    assert src.prefetched == [4, 4, 1]  # 9 entity / 청크 4


def test_fuzzy_source_uses_fixed_canonical(tmp_path):
    pytest.importorskip("rapidfuzz")
    path = tmp_path / "map.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["medicinalproduct", "ingredient_norm", "ingredient_set", "unii",
                    "source", "method", "confidence", "dictionary_version"])
        # 성분 AMLODIPINE이 복합제 행에서 먼저 등장 → #43 수정 전이면 복합제 묶음이 붙던 것
        w.writerow(["AMLO HCTZ OLM", "AMLODIPINE", "amlodipine|hydrochlorothiazide|olmesartan medoxomil",
                    "1J444QC288", "t", "사전", "", h.DICTIONARY_VERSION])
        w.writerow(["INSUPROD", "INSULIN LISPRO", "insulin lispro|water ; insulin lispro", "", "t", "사전", "",
                    h.DICTIONARY_VERSION])
    src = h.FuzzySource(path, min_score=80)
    cands = {c.alias: c for c in src.candidates("AMLODIPNE")}
    assert cands["AMLODIPINE"].ingredient_set == "amlodipine"
    assert cands["AMLODIPINE"].lexical_score >= 80 and cands["AMLODIPINE"].source == "fuzzy"
    assert all(" ; " not in c.ingredient_set for c in src.candidates("INSUPROD"))  # 충돌 보존값 제외


# ── DiAna 채점 ────────────────────────────────────────────────────────────────

def test_load_gold_groups_combo_rows(tmp_path):
    p = tmp_path / "dev.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["raw_drug_name", "ingredient_norm", "unii", "source"])
        w.writerow(["Symbyax", "OLANZAPINE", "", "diana"])
        w.writerow(["Symbyax", "FLUOXETINE HYDROCHLORIDE", "", "diana"])
        w.writerow(["klacid", "CLARITHROMYCIN", "", "diana"])
    assert h.load_gold(p) == {"SYMBYAX": "fluoxetine|olanzapine", "KLACID": "clarithromycin"}


def test_evaluate_scores_against_gold(tmp_path, fixture_source):
    storage = h.LocalStorage(str(tmp_path / "run"))
    h.run(_entities(), [fixture_source], ScriptedJudge(RESPONSES), storage, log=lambda *_: None)
    gold = {"KLACID": "clarithromycin",                  # MATCH 정답
            "SYMBYAX": "fluoxetine|olanzapine",          # MULTI 정답
            "SINEMET": "carbidopa|entacapone|levodopa",  # MATCH 오답(후보에 정답 없음)
            "ADVIL": "ibuprofen",                        # JSON 아님 → NONE, 후보엔 정답 있음
            "XYZFAKEDRUG": "fakeine"}                    # 후보 없음 → NONE
    r = h.evaluate(storage, gold, tmp_path / "details.csv")
    assert r["n_gold_entities"] == 5
    assert r["decisions"] == {"MATCH": 2, "MULTI": 1, "NONE": 2}
    assert r["correct"] == 2
    assert r["auto_precision"] == round(2 / 3, 4)
    assert r["accuracy"] == 0.4 and r["none_rate"] == 0.4
    assert r["cand_recall"] == 0.4          # KLACID·ADVIL만 후보에 정답 묶음 그대로
    assert r["cand_recall_union"] == 0.6    # SYMBYAX는 후보 성분 합치면 포함
    rows = _read_csv(tmp_path / "details.csv")
    assert {r["raw_drug_name"]: r["correct"] for r in rows}["KLACID"] == "1"


def test_cli_gold_filters_entities(tmp_path):
    inp = tmp_path / "unknown.csv"
    with open(inp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "n_reports"])
        w.writerows(SAMPLE_ROWS)
    dev = tmp_path / "dev.csv"
    with open(dev, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["raw_drug_name", "ingredient_norm", "unii", "source"])
        w.writerow(["KLACID 500 MG", "CLARITHROMYCIN", "", "diana"])  # entity의 두 번째 raw 이름으로도 매칭
        w.writerow(["ADVIL", "IBUPROFEN", "", "diana"])
    cands = tmp_path / "cands.json"
    cands.write_text(json.dumps(FIXTURE), encoding="utf-8")
    unmatched = _write_unmatched(tmp_path, ["KLACID 500 MG", "ADVIL"])
    out = tmp_path / "run"
    h.main(["run", str(inp), "--out", str(out), "--fixture-candidates", str(cands), "--gold", str(dev),
            "--unmatched", str(unmatched)])
    assert {r["normalized"] for r in h.iter_records(h.LocalStorage(str(out)))} == {"KLACID", "ADVIL"}


def _write_unmatched(tmp_path, names):
    p = tmp_path / "unmatched_for_stage3.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name"])
        w.writerows([n] for n in names)
    return p


def test_restrict_gold_to_stage3_drops_leaked_names(tmp_path):
    gold = {"KLACID": "clarithromycin", "ASPIRIN": "aspirin"}  # ASPIRIN은 사전에 있어 1단계가 처리
    unmatched = _write_unmatched(tmp_path, ["klacid "])          # 대소문자·공백 무시
    assert h.restrict_gold_to_stage3(gold, unmatched) == {"KLACID": "clarithromycin"}
    with pytest.raises(FileNotFoundError):
        h.restrict_gold_to_stage3(gold, tmp_path / "missing.csv")


def test_cli_evaluate_scores_only_stage3_input(tmp_path, fixture_source, capsys):
    out = tmp_path / "run"
    h.run(_entities(), [fixture_source], ScriptedJudge(RESPONSES), h.LocalStorage(str(out)), log=lambda *_: None)
    dev = tmp_path / "dev.csv"
    with open(dev, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["raw_drug_name", "ingredient_norm", "unii", "source"])
        w.writerow(["KLACID", "CLARITHROMYCIN", "", "diana"])
        w.writerow(["ADVIL", "IBUPROFEN", "", "diana"])
        w.writerow(["NOTRUN", "FAKEINE", "", "diana"])
    unmatched = _write_unmatched(tmp_path, ["KLACID", "NOTRUN"])  # ADVIL은 1단계가 처리 → 채점 제외
    h.main(["evaluate", "--out", str(out), "--gold", str(dev), "--unmatched", str(unmatched)])
    r = json.loads(capsys.readouterr().out)
    assert r["eval_scope"] == {"gold_file": "dev.csv", "unmatched_file": "unmatched_for_stage3.csv",
                               "gold_names": 3, "gold_names_stage3": 2, "evaluated_entities": 1,
                               "excluded": {"not_in_stage3_input": 1, "not_in_run": 1}}
    assert r["n_gold_entities"] == 1 and r["correct"] == 1


def test_run_and_evaluate_use_same_stage3_gold(tmp_path, capsys):
    inp = tmp_path / "unknown.csv"
    with open(inp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "n_reports"])
        w.writerows(SAMPLE_ROWS)
    dev = tmp_path / "dev.csv"
    with open(dev, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["raw_drug_name", "ingredient_norm", "unii", "source"])
        w.writerow(["KLACID", "CLARITHROMYCIN", "", "diana"])
        w.writerow(["ADVIL", "IBUPROFEN", "", "diana"])
    unmatched = _write_unmatched(tmp_path, ["KLACID"])
    cands = tmp_path / "cands.json"
    cands.write_text(json.dumps(FIXTURE), encoding="utf-8")
    out = tmp_path / "run"
    common = ["--out", str(out), "--gold", str(dev), "--unmatched", str(unmatched)]
    h.main(["run", str(inp), "--fixture-candidates", str(cands), *common])
    assert {r["normalized"] for r in h.iter_records(h.LocalStorage(str(out)))} == {"KLACID"}  # ADVIL 안 돌림
    capsys.readouterr()
    h.main(["evaluate", *common])
    scope = json.loads(capsys.readouterr().out)["eval_scope"]
    assert scope["gold_names_stage3"] == 1 and scope["excluded"] == {"not_in_stage3_input": 1, "not_in_run": 0}
