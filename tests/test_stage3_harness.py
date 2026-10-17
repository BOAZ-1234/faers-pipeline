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
