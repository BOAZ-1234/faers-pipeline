"""
C단계 3단계 하네스 뼈대 — 후보 합치기 → LLM 판정 → candidate/resolution 저장 (이슈 #51, 레인 ②)

3단계 설계 §5 #2·#3. 3단계를 끝까지 꿰는 실험대다. 같은 하네스에 프롬프트만 바꿔 넣고
DiAna dev로 채점해 비교하는 게 목적이라(설계 §5 "1명이 하네스, 3명이 프롬프트"), 부품은 전부
갈아 끼울 수 있게 인터페이스로 둔다.

  triage UNKNOWN CSV (name,n_reports)
   → 정규화명(normalize_query, 1단계와 동일) 기준으로 묶기 — entity_id = 정규화명 기준
     (계약 회의: raw 대비 7% 호출 절감. raw 이름은 raw_map으로 추적)
   → 후보 생성기들(3-A 임베딩 검색, 2단계 fuzzy) 결과를 합치기 — 같은 성분 묶음은 하나로 접고 Top-K
   → 후보가 없으면 LLM을 부르지 않고 NONE (호출 절감)
   → LLM closed decision: MATCH / MULTI / NONE + 후보 번호만 (자유생성 금지, 설계 §6-3)
   → ingredient_set은 **코드가** 조립 (복합제도, 설계 결정 8)
   → candidate / resolution 행 — dbt contract(stg_candidate/stg_resolution) 컬럼 그대로
   → 청크마다 part 파일로 저장(로컬 또는 s3://). 재실행하면 이미 끝난 entity는 건너뛴다
     (spot이 2분 경고 후 꺼져도 이어서 돌기 위한 것, 설계 §4)

지금 main에 없는 부품은 가짜로 둔다 — 머지되는 대로 CandidateSource/Judge 구현만 추가하면 된다.
  - 3-A 임베딩 검색(#47 FAISS)  → FixtureSource(JSON)로 대체
  - 2단계 fuzzy 후보(#43)        → 미연결
  - vLLM                         → MockJudge, 또는 OpenAI 호환 엔드포인트(OpenAICompatJudge)
LLM 호출은 OpenAI 호환 프로토콜(vLLM이 스스로 띄우는 것)로만 한다 — 외부 LLM API는 쓰지 않는다(설계 §4).

상태 매핑 (v1): LLM 자기확신으로 자동 확정하지 않는다(설계 결정 9). 자동 확정 규칙은 dev 채점 후 정한다.
  MATCH / MULTI → 보류 (성분은 채워 둠)     NONE → 미해결

실행:
  python3 stage3_harness.py run out/stage3_unknown.csv --out out/stage3_run --judge mock \\
          --fixture-candidates cands.json --limit 20
  python3 stage3_harness.py run out/stage3_unknown.csv --out s3://<bucket>/stage3/run1 \\
          --judge openai --base-url http://localhost:8000/v1 --model Qwen2.5-7B-Instruct-AWQ
  python3 stage3_harness.py finalize --out out/stage3_run --dest out/stage3_run/final
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from normalize import normalize_query
from build_map import DICTIONARY_VERSION

PIPELINE_VERSION = "c-stage3-v0"
STAGE = 3
DEFAULT_PROMPT = HERE / "prompts" / "stage3_judge_v1.txt"
DECISIONS = ("MATCH", "MULTI", "NONE")
STATUS_BY_DECISION = {"MATCH": "보류", "MULTI": "보류", "NONE": "미해결"}

# routing_reason = "이 이름이 **왜 3단계까지 내려왔나**"(진입 사유). LLM 판정 결과(decision)와는 별개라
# routing=NO_MATCH인데 decision=MATCH인 레코드는 정상이다 — "1·2단계는 못 찾았고, LLM이 찾았다"는 뜻.
# 값은 #47 langfuse_tracer.ROUTING과 같은 이름을 쓴다. 2단계 보류 사유(FUZZY_LOW_MARGIN·FUZZY_CONFLICT)는
# #43 연결 시 2단계 결과를 받아 세분화한다(PR#52 리뷰).
ROUTING_NO_MATCH = "NO_MATCH"            # 1·1.5·2단계 모두 후보 없음/기각 → triage UNKNOWN
ROUTING_NO_CANDIDATES = "NO_CANDIDATES"  # 3단계 후보 생성기도 후보 0개 → LLM 미호출(하네스 전용)

# dbt contract(contracts/dbt/models/staging/schema.yml)와 같은 컬럼·순서
CANDIDATE_FIELDS = [
    "entity_id", "raw_drug_name", "normalized_drug_name", "stage", "candidate_rank",
    "candidate_alias", "candidate_ingredient_set", "unii", "lexical_score", "semantic_score", "source",
]
RESOLUTION_FIELDS = [
    "entity_id", "raw_drug_name", "resolution_status", "resolved_ingredient_set", "unii",
    "resolved_stage", "resolved_method", "confidence", "pipeline_version", "dictionary_version",
]
RAW_MAP_FIELDS = ["raw_drug_name", "entity_id", "n_reports"]


# ── 입력 → entity ──────────────────────────────────────────────────────────────

@dataclass
class Entity:
    entity_id: str
    normalized: str
    raw_names: list  # [(raw_name, n_reports)] 신고 많은 순
    n_reports: int

    @property
    def representative(self) -> str:
        return self.raw_names[0][0]


def entity_id_for(normalized: str) -> str:
    """정규화명 → 결정적 ID. 재실행·다른 머신에서도 같은 값이어야 재개·조인이 된다."""
    return "s3-" + hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def load_rows(path: Path) -> list[tuple[str, int]]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            name = (r.get("name") or "").strip()
            if not name:
                continue
            try:
                n = int(r.get("n_reports") or 0)
            except ValueError:
                n = 0
            rows.append((name, n))
    return rows


def group_entities(rows: list[tuple[str, int]]) -> list[Entity]:
    """같은 정규화명으로 떨어지는 raw 이름들을 한 entity로 묶는다(LLM은 entity당 1번).
    정규화가 전부 떼어내 빈 문자열이 되면("500 MG" 등) raw 대문자를 그대로 키로 쓴다."""
    groups: dict[str, list] = {}
    for name, n in rows:
        key = normalize_query(name) or name.strip().upper()
        groups.setdefault(key, []).append((name, n))
    entities = []
    for key, raws in groups.items():
        raws.sort(key=lambda x: (-x[1], x[0]))
        entities.append(Entity(entity_id_for(key), key, raws, sum(n for _, n in raws)))
    entities.sort(key=lambda e: (-e.n_reports, e.normalized))
    return entities


# ── 후보 생성기 ────────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    alias: str
    ingredient_set: str
    unii: str = ""
    lexical_score: float | None = None
    semantic_score: float | None = None
    source: str = ""


class NoSource:
    """후보 없음 — 배관만 볼 때."""
    name = "none"

    def candidates(self, normalized: str) -> list[Candidate]:
        return []


class FixtureSource:
    """JSON {정규화명: [{alias, ingredient_set, unii?, lexical_score?, semantic_score?}]} — 3-A 검색이
    main에 오기 전까지 쓰는 가짜 검색. 손으로 만든 샘플로 배관과 프롬프트를 시험한다."""

    def __init__(self, path: Path, name: str = "fixture"):
        self.name = name
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        self._by_name = {k.strip().upper(): v for k, v in raw.items()}

    def candidates(self, normalized: str) -> list[Candidate]:
        return [Candidate(alias=c["alias"], ingredient_set=c["ingredient_set"], unii=c.get("unii", ""),
                          lexical_score=c.get("lexical_score"), semantic_score=c.get("semantic_score"),
                          source=self.name)
                for c in self._by_name.get(normalized, [])]


def union_candidates(lists: list[list[Candidate]], top_k: int) -> list[Candidate]:
    """여러 생성기 후보를 합친다. 같은 성분 묶음을 가리키는 후보는 하나로 접는다
    (alias-level로 찾고 canonical 성분으로 collapse, 설계 결정 2). 점수는 생성기별 최댓값을 남기고,
    의미 점수 → 글자 점수 순으로 정렬해 Top-K만 LLM에 준다."""
    merged: dict[str, Candidate] = {}
    for cands in lists:
        for c in cands:
            key = c.ingredient_set.strip().lower()
            if not key:
                continue
            cur = merged.get(key)
            if cur is None:
                merged[key] = Candidate(c.alias, key, c.unii, c.lexical_score, c.semantic_score, c.source)
                continue
            cur.lexical_score = _max(cur.lexical_score, c.lexical_score)
            cur.semantic_score = _max(cur.semantic_score, c.semantic_score)
            if c.source and c.source not in cur.source.split("+"):
                cur.source = "+".join(sorted(filter(None, cur.source.split("+") + [c.source])))
            cur.unii = cur.unii or c.unii
    ranked = sorted(merged.values(), key=lambda c: (
        -(c.semantic_score if c.semantic_score is not None else -1.0),
        -(c.lexical_score if c.lexical_score is not None else -1.0),
        c.ingredient_set))
    return ranked[:top_k]


def _max(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


# ── LLM 판정 ──────────────────────────────────────────────────────────────────

@dataclass
class Decision:
    decision: str
    candidate_ids: list = field(default_factory=list)  # 1부터 시작하는 후보 번호
    note: str = ""
    prompt: str = ""
    response: str = ""
    usage: dict = field(default_factory=dict)


def render_user_message(entity: Entity, cands: list[Candidate]) -> str:
    """LLM에 주는 재료: 원본명·정규화명·후보(alias·성분·UNII·점수). 후보가 어느 단계에서 왔는지 같은
    정답을 유추시킬 메타데이터는 주지 않는다(컨닝 방지, 설계 결정 5)."""
    lines = [f"Raw drug name: {entity.representative}",
             f"Normalized: {entity.normalized}",
             "Candidates:"]
    for i, c in enumerate(cands, 1):
        parts = [f"alias={c.alias}", f"ingredients={c.ingredient_set}"]
        if c.unii:
            parts.append(f"unii={c.unii}")
        if c.lexical_score is not None:
            parts.append(f"spelling_similarity={c.lexical_score:.1f}")
        lines.append(f"{i}. " + " | ".join(parts))
    return "\n".join(lines)


_JSON_OBJ = re.compile(r"\{.*\}", re.S)


def parse_decision(text: str, n_candidates: int) -> Decision:
    """LLM 응답 → Decision. 형식이 틀리면 확정하지 않고 NONE + 사유로 떨어뜨린다
    (부작용 사전 3단계의 'silent empty 방지·shape 검증'과 같은 원칙)."""
    m = _JSON_OBJ.search(text or "")
    if not m:
        return Decision("NONE", note="invalid: no JSON object")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return Decision("NONE", note="invalid: JSON parse error")
    if not isinstance(obj, dict):
        return Decision("NONE", note="invalid: not an object")
    decision = str(obj.get("decision", "")).strip().upper()
    ids = obj.get("candidate_ids", [])
    note = str(obj.get("note", ""))[:300]
    if decision not in DECISIONS:
        return Decision("NONE", note=f"invalid: decision={decision!r}")
    if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        return Decision("NONE", note="invalid: candidate_ids not int list")
    ids = sorted(set(ids))
    if any(i < 1 or i > n_candidates for i in ids):
        return Decision("NONE", note=f"invalid: candidate id out of range 1..{n_candidates}")
    if decision == "NONE" and ids:
        return Decision("NONE", note="invalid: NONE with ids")
    if decision == "MATCH" and len(ids) != 1:
        return Decision("NONE", note=f"invalid: MATCH needs 1 id, got {len(ids)}")
    if decision == "MULTI" and len(ids) < 2:
        return Decision("NONE", note=f"invalid: MULTI needs >=2 ids, got {len(ids)}")
    return Decision(decision, ids, note)


class MockJudge:
    """배관 디버깅용. 정규화명과 alias가 같으면 MATCH, 아니면 NONE. 정확도 측정에 쓰지 않는다."""
    model = "mock"

    def __init__(self, prompt_version: str = "mock"):
        self.prompt_version = prompt_version

    def judge(self, entity: Entity, cands: list[Candidate]) -> Decision:
        for i, c in enumerate(cands, 1):
            if c.alias.strip().upper() == entity.normalized:
                return Decision("MATCH", [i], "mock: alias == normalized")
        return Decision("NONE", note="mock: no exact alias")


def _post_json(url: str, payload: dict, timeout: float, api_key: str) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class OpenAICompatJudge:
    """vLLM이 띄우는 OpenAI 호환 /chat/completions 엔드포인트로 판정한다.
    프롬프트(시스템 메시지)는 파일로 받는다 — 파일 이름(stem)이 prompt_version이 된다."""

    def __init__(self, base_url: str, model: str, prompt_path: Path,
                 temperature: float = 0.0, max_tokens: int = 200, timeout: float = 60.0, retries: int = 3):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.system = prompt_path.read_text(encoding="utf-8")
        self.prompt_version = prompt_path.stem
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.retries = retries
        self.api_key = os.environ.get("STAGE3_LLM_API_KEY", "EMPTY")  # vLLM 기본은 키 검사 없음

    def judge(self, entity: Entity, cands: list[Candidate]) -> Decision:
        user = render_user_message(entity, cands)
        payload = {"model": self.model, "temperature": self.temperature, "max_tokens": self.max_tokens,
                   "messages": [{"role": "system", "content": self.system},
                                {"role": "user", "content": user}]}
        last_err = None
        for attempt in range(self.retries):
            try:
                resp = _post_json(self.url, payload, self.timeout, self.api_key)
                break
            except Exception as e:  # 네트워크·서버 일시 오류는 재시도, 끝내 실패하면 예외로 멈춘다
                last_err = e
                time.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"LLM 호출 실패 ({self.url}): {last_err}")
        text = resp["choices"][0]["message"]["content"]
        u = resp.get("usage") or {}
        d = parse_decision(text, len(cands))
        d.prompt, d.response = user, text
        d.usage = {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0)}
        return d


# ── 판정 → 행 ─────────────────────────────────────────────────────────────────

def assemble_ingredient_set(chosen: list[Candidate]) -> str:
    """고른 후보들의 성분을 합쳐 조인키 형식(소문자·정렬·| 연결)으로 만든다. 단일제도 원소 1개 묶음."""
    parts = set()
    for c in chosen:
        for p in c.ingredient_set.split("|"):
            p = p.strip().lower()
            if p:
                parts.add(p)
    return "|".join(sorted(parts))


def build_record(entity: Entity, cands: list[Candidate], d: Decision, routing_reason: str,
                 meta: dict) -> dict:
    chosen = [cands[i - 1] for i in d.candidate_ids]
    ingredient_set = assemble_ingredient_set(chosen) if d.decision != "NONE" else ""
    unii = chosen[0].unii if d.decision == "MATCH" and chosen[0].unii else ""
    resolution = {
        "entity_id": entity.entity_id,
        "raw_drug_name": entity.representative,
        "resolution_status": STATUS_BY_DECISION[d.decision],
        "resolved_ingredient_set": ingredient_set,
        "unii": unii,
        "resolved_stage": STAGE,
        "resolved_method": "llm" if routing_reason != ROUTING_NO_CANDIDATES else "none",
        "confidence": None,  # v1: LLM 자기확신을 쓰지 않음(설계 결정 9)
        "pipeline_version": meta["pipeline_version"],
        "dictionary_version": meta["dictionary_version"],
    }
    candidates = [{
        "entity_id": entity.entity_id,
        "raw_drug_name": entity.representative,
        "normalized_drug_name": entity.normalized,
        "stage": STAGE,
        "candidate_rank": rank,
        "candidate_alias": c.alias,
        "candidate_ingredient_set": c.ingredient_set,
        "unii": c.unii,
        "lexical_score": c.lexical_score,
        "semantic_score": c.semantic_score,
        "source": c.source,
    } for rank, c in enumerate(cands, 1)]
    return {
        "entity_id": entity.entity_id,
        "normalized": entity.normalized,
        "raw_names": entity.raw_names,
        "n_reports": entity.n_reports,
        "routing_reason": routing_reason,  # 진입 사유. 판정 결과는 아래 decision
        "decision": d.decision,
        "candidate_ids": d.candidate_ids,
        "note": d.note,
        "meta": meta,
        "resolution": resolution,
        "candidates": candidates,
    }


# ── Langfuse (선택) ───────────────────────────────────────────────────────────

class NullTracer:
    @contextmanager
    def trace(self, entity: Entity, cands: list[Candidate], meta: dict, routing_reason: str):
        yield None

    def record(self, handle, d: Decision, ingredient_set: str, model: str) -> None:
        pass

    def flush(self) -> None:
        pass


class LangfuseTracerAdapter:
    """#47의 langfuse_tracer(LLM 판정 1건 = Trace 1개, 설계 §7)를 감싼다. 그 모듈이 main에 오기 전엔
    --trace를 쓰지 않는다. 비용 상한 때문에 전량이 아니라 실험·디버깅분에만 켠다(설계 §4)."""

    def __init__(self):
        import langfuse_tracer  # #47
        self._mod = langfuse_tracer
        self._tracer = langfuse_tracer.LangfuseTracer()

    @contextmanager
    def trace(self, entity: Entity, cands: list[Candidate], meta: dict, routing_reason: str):
        lf_meta = self._mod.make_meta(
            raw_name=entity.representative, normalized_name=entity.normalized,
            routing_reason=routing_reason, embedding_top_k=len(cands),
            candidate_ids=[c.ingredient_set for c in cands],
            pipeline_version=meta["pipeline_version"], dictionary_version=meta["dictionary_version"],
            retrieval_version=meta["retrieval_version"], prompt_version=meta["prompt_version"],
            llm_model=meta["llm_model"])
        with self._tracer.trace(entity.representative, lf_meta) as t:
            yield t

    def record(self, handle, d: Decision, ingredient_set: str, model: str) -> None:
        if handle is None:
            return
        if d.prompt:
            handle.log_generation(d.prompt, d.response, d.usage, model=model)
        handle.log_output(d.decision, ingredient_set)

    def flush(self) -> None:
        self._tracer.flush()


# ── 저장 (로컬 / S3) ──────────────────────────────────────────────────────────

class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root)

    def list_parts(self) -> list[str]:
        d = self.root / "parts"
        return sorted(p.name for p in d.glob("part-*.jsonl")) if d.exists() else []

    def read_part(self, name: str) -> str:
        return (self.root / "parts" / name).read_text(encoding="utf-8")

    def write_part(self, name: str, text: str) -> None:
        d = self.root / "parts"
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(d / name)  # 쓰다 꺼져도 반쪽 part가 남지 않게


class S3Storage:
    """s3://bucket/prefix — EC2(GPU) 인스턴스 역할로 인증. part 하나 = 객체 하나라 반쪽 쓰기가 없다."""

    def __init__(self, uri: str):
        import boto3
        bucket, _, prefix = uri[len("s3://"):].partition("/")
        self.bucket, self.prefix = bucket, prefix.rstrip("/") + "/parts/"
        self.s3 = boto3.client("s3")

    def list_parts(self) -> list[str]:
        names = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                name = obj["Key"][len(self.prefix):]
                if name.startswith("part-") and name.endswith(".jsonl"):
                    names.append(name)
        return sorted(names)

    def read_part(self, name: str) -> str:
        return self.s3.get_object(Bucket=self.bucket, Key=self.prefix + name)["Body"].read().decode("utf-8")

    def write_part(self, name: str, text: str) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=self.prefix + name, Body=text.encode("utf-8"))


def open_storage(out: str):
    return S3Storage(out) if out.startswith("s3://") else LocalStorage(out)


def iter_records(storage):
    for name in storage.list_parts():
        for line in storage.read_part(name).splitlines():
            if line.strip():
                yield json.loads(line)


def done_entity_ids(storage) -> set[str]:
    return {r["entity_id"] for r in iter_records(storage)}


# ── run / finalize ────────────────────────────────────────────────────────────

def run(entities: list[Entity], sources: list, judge, storage, *, top_k: int = 10, chunk_size: int = 200,
        retrieval_version: str = "none", tracer=None, concurrency: int = 1, log=print) -> dict:
    """entity들을 판정해 chunk_size마다 part로 저장한다. 이미 저장된 entity는 건너뛴다(재개).

    concurrency>1이면 LLM 호출을 ThreadPoolExecutor로 병렬화한다. vLLM은 continuous batching을
    쓰므로 동시 요청이 많아야 GPU를 채운다 (1 요청씩 보내면 GPU 유휴). I/O bound(서버 응답 대기)라
    asyncio 대신 스레드로 충분하고, 기존 동기 트레이서/저장 로직을 그대로 둘 수 있다.
    저장은 완료된 순서(응답 순서)로 쌓되 part는 chunk_size마다 flush — Spot 중단 시 손실 최소화."""
    tracer = tracer or NullTracer()
    done = done_entity_ids(storage)
    todo = [e for e in entities if e.entity_id not in done]
    meta = {"pipeline_version": PIPELINE_VERSION, "dictionary_version": DICTIONARY_VERSION,
            "retrieval_version": retrieval_version, "prompt_version": judge.prompt_version,
            "llm_model": judge.model}
    log(f"entity {len(entities):,}개 중 이미 완료 {len(entities) - len(todo):,}개 → 이번에 {len(todo):,}개"
        + (f" (concurrency={concurrency})" if concurrency > 1 else ""))

    # 실행마다 고유해야 한다 — 시각+PID만 쓰면 같은 프로세스가 1초 안에 재실행할 때 앞선 part를 덮어쓴다
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    stats = {"processed": 0, "llm_calls": 0, **{k: 0 for k in DECISIONS}}
    buf: list[str] = []
    seq = 0

    def flush_buf():
        nonlocal seq, buf
        if buf:
            storage.write_part(f"part-{run_id}-{seq:05d}.jsonl", "\n".join(buf) + "\n")
            seq += 1
            buf = []

    def process_one(e: Entity) -> tuple[dict, str, Decision]:
        """후보 생성 + LLM 판정 + 레코드 조립. 순수 함수 — stats·buf는 메인 스레드가 집계."""
        cands = union_candidates([s.candidates(e.normalized) for s in sources], top_k)
        if not cands:
            routing, d = ROUTING_NO_CANDIDATES, Decision("NONE", note="no candidates — LLM not called")
            rec = build_record(e, cands, d, routing, meta)
            return rec, routing, d
        routing = ROUTING_NO_MATCH
        with tracer.trace(e, cands, meta, routing) as handle:
            d = judge.judge(e, cands)
            rec = build_record(e, cands, d, routing, meta)
            tracer.record(handle, d, rec["resolution"]["resolved_ingredient_set"], judge.model)
        return rec, routing, d

    def collect(rec, routing, d):
        if routing != ROUTING_NO_CANDIDATES:
            stats["llm_calls"] += 1
        stats[d.decision] += 1
        stats["processed"] += 1
        buf.append(json.dumps(rec, ensure_ascii=False))
        if len(buf) >= chunk_size:
            flush_buf()

    if concurrency <= 1:
        for e in todo:
            collect(*process_one(e))
    else:
        # ThreadPoolExecutor로 동시 호출. as_completed로 완료 순서대로 집계·저장.
        # 결과 순서와 무관하게 entity_id가 각 레코드에 들어 있어 finalize 단계에서 멱등.
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = {ex.submit(process_one, e): e for e in todo}
            try:
                for fut in as_completed(futures):
                    collect(*fut.result())
            except BaseException as err:
                # KeyboardInterrupt(Spot 중단 2분 전 통지)·judge 재시도 소진 등 모든 예외에서
                # 큐에 쌓인 future를 취소해야 한다. with 블록은 종료 시 제출된 모든 future를
                # 기다리므로, 취소 안 하면 "실패했는데 수천 건을 더 돌리고 종료" — GPU 돈·시간
                # 낭비. cancel()은 아직 시작 안 한 것만 중단(이미 돌고 있는 건 그대로 완료).
                log(f"중단 ({type(err).__name__}) — 저장된 part는 보존, 대기 중 요청 취소")
                for f in futures:
                    f.cancel()
                raise
    flush_buf()
    tracer.flush()
    log(f"완료: {stats}")
    return stats


def finalize(storage, dest: Path) -> dict:
    """part들 → candidate.csv / resolution.csv / raw_map.csv (dbt contract 컬럼).
    같은 entity가 여러 part에 있으면(동시 실행 등) 먼저 저장된 것 하나만 쓴다."""
    seen: set[str] = set()
    res_rows, cand_rows, raw_rows = [], [], []
    for rec in iter_records(storage):
        if rec["entity_id"] in seen:
            continue
        seen.add(rec["entity_id"])
        res_rows.append(rec["resolution"])
        cand_rows.extend(rec["candidates"])
        raw_rows.extend({"raw_drug_name": name, "entity_id": rec["entity_id"], "n_reports": n}
                        for name, n in rec["raw_names"])
    dest.mkdir(parents=True, exist_ok=True)
    for fname, fields, rows in (("resolution.csv", RESOLUTION_FIELDS, res_rows),
                                ("candidate.csv", CANDIDATE_FIELDS, cand_rows),
                                ("raw_map.csv", RAW_MAP_FIELDS, raw_rows)):
        with open(dest / fname, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows({k: ("" if v is None else v) for k, v in r.items()} for r in rows)
    return {"resolution": len(res_rows), "candidate": len(cand_rows), "raw_map": len(raw_rows)}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="C단계 3단계 하네스 (레인 ②)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="판정 실행(재개 가능)")
    r.add_argument("input", type=Path, help="triage UNKNOWN CSV (name,n_reports)")
    r.add_argument("--out", required=True, help="part 저장 위치 — 로컬 폴더 또는 s3://bucket/prefix")
    r.add_argument("--judge", choices=("mock", "openai"), default="mock")
    r.add_argument("--base-url", default=os.environ.get("STAGE3_LLM_BASE_URL", "http://localhost:8000/v1"))
    r.add_argument("--model", default=os.environ.get("STAGE3_LLM_MODEL", ""))
    r.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT, help="시스템 프롬프트 파일(stem=prompt_version)")
    r.add_argument("--fixture-candidates", type=Path, help="가짜 검색 JSON {정규화명: [후보...]}")
    r.add_argument("--top-k", type=int, default=10, help="LLM에 줄 후보 수(3-A 운영점 K=10)")
    r.add_argument("--min-reports", type=int, default=1, help="신고 ≥N건 entity만(운영점, #48 곡선)")
    r.add_argument("--limit", type=int, help="앞에서 N개 entity만(신고 많은 순)")
    r.add_argument("--chunk-size", type=int, default=200, help="part 하나당 entity 수(재개 단위)")
    r.add_argument("--concurrency", type=int, default=1,
                   help="동시 LLM 호출 수(ThreadPoolExecutor). vLLM continuous batching 활용 — "
                        "GPU에서는 8/16/32/64 sweep 후 cost per 1,000으로 운영점 결정. 1이면 순차.")
    r.add_argument("--trace", action="store_true", help="Langfuse trace(#47 langfuse_tracer 필요)")

    f = sub.add_parser("finalize", help="part들 → candidate/resolution/raw_map CSV")
    f.add_argument("--out", required=True, help="run의 --out과 같은 위치")
    f.add_argument("--dest", type=Path, required=True, help="CSV를 쓸 로컬 폴더")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    storage = open_storage(args.out)
    if args.cmd == "finalize":
        print(finalize(storage, args.dest))
        return

    entities = [e for e in group_entities(load_rows(args.input)) if e.n_reports >= args.min_reports]
    if args.limit:
        entities = entities[:args.limit]
    sources = [FixtureSource(args.fixture_candidates)] if args.fixture_candidates else [NoSource()]
    retrieval_version = "fixture" if args.fixture_candidates else "none"
    if args.judge == "openai":
        if not args.model:
            sys.exit("--model 또는 STAGE3_LLM_MODEL 필요")
        judge = OpenAICompatJudge(args.base_url, args.model, args.prompt)
    else:
        judge = MockJudge()
    tracer = LangfuseTracerAdapter() if args.trace else None
    run(entities, sources, judge, storage, top_k=args.top_k, chunk_size=args.chunk_size,
        retrieval_version=retrieval_version, tracer=tracer, concurrency=args.concurrency)


if __name__ == "__main__":
    main()
