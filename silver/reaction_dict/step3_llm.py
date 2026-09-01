"""
3단계 LLM 판정 — step2 후보 목록에서 정답 PT(묶음) 선택
입력: output/step2_candidates.csv (33개)
출력: output/step3_llm.csv
  signal, pt_set (파이프 구분 소문자 정렬), method=llm, note
"""
import csv
import json
import os
import time
from pathlib import Path

import anthropic

HERE = Path(__file__).parent
CANDIDATES_PATH = HERE / "output" / "step2_candidates.csv"
OUT_PATH = HERE / "output" / "step3_llm.csv"

# 이미 처리된 것은 재실행 시 스킵
DONE: dict[str, dict] = {}
if OUT_PATH.exists():
    with open(OUT_PATH) as f:
        for row in csv.DictReader(f):
            DONE[row["signal"]] = row

SYSTEM = """\
You are a pharmacovigilance expert. Your task: map an FDA signal term to the
exact MedDRA Preferred Term(s) used in FAERS reporting.

Rules:
1. Return ONLY the MedDRA PT(s) that best represent the signal. Use the exact
   spelling from the candidate list (ALL CAPS as shown).
2. If multiple PTs form a standard clinical group for this signal (e.g. SJS/TEN/
   DRESS/AGEP for "Severe cutaneous adverse reactions"), return all of them.
3. If exactly one PT matches, return just that one.
4. If none of the candidates match the signal, return the string "NONE".
5. Ignore candidates that merely share a word but are clinically unrelated
   (e.g. RENAL IMPAIRMENT for "Hearing impairment").
6. Output ONLY valid JSON, no prose:
   {"pts": ["PT_ONE", "PT_TWO"], "note": "one sentence rationale"}
"""


def ask_llm(client: anthropic.Anthropic, signal: str, candidates: str) -> dict:
    prompt = f"""Signal: {signal}

Candidate MedDRA PTs (name(count)):
{candidates}

Which PT(s) from the candidate list map to this signal? Follow the rules."""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
        else:
            return {"pts": [], "note": f"parse error: {raw[:120]}"}

    if not isinstance(parsed, dict):
        return {"pts": [], "note": f"unexpected response shape: {raw[:120]}"}
    pts = parsed.get("pts", [])
    if isinstance(pts, str):
        pts = [] if pts.upper() == "NONE" else [pts]
    return {"pts": [p for p in pts if p and p.upper() != "NONE"], "note": parsed.get("note", "")}


def pts_to_set_id(pts: list[str]) -> str:
    """['STEVENS-JOHNSON SYNDROME', 'TOXIC EPIDERMAL NECROLYSIS'] → 'stevens-johnson syndrome|toxic epidermal necrolysis'"""
    return "|".join(sorted(p.lower() for p in pts))


def main():
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    with open(CANDIDATES_PATH) as f:
        rows = list(csv.DictReader(f))

    results = list(DONE.values())
    new_count = 0

    for row in rows:
        signal = row["signal"]
        if signal in DONE:
            print(f"  [skip] {signal}")
            continue

        candidates = row["candidates"]
        n = int(row["n_candidates"])

        if n == 0:
            result = {
                "signal": signal,
                "pt_set": "",
                "method": "no_candidates",
                "note": "후보 없음 — 수동 처리 필요",
            }
            print(f"  [no candidates] {signal}")
        else:
            print(f"  [LLM] {signal} ...", end=" ", flush=True)
            resp = ask_llm(client, signal, candidates)
            pts = [p for p in resp.get("pts", []) if p and p != "NONE"]
            result = {
                "signal": signal,
                "pt_set": pts_to_set_id(pts) if pts else "",
                "method": "llm",
                "note": resp.get("note", ""),
            }
            status = result["pt_set"] or "NONE"
            print(status)
            time.sleep(0.3)

        results.append(result)
        new_count += 1

        # 매 건마다 중간 저장
        with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["signal", "pt_set", "method", "note"])
            writer.writeheader()
            writer.writerows(results)

    print(f"\n완료: {new_count}개 처리 → {OUT_PATH.relative_to(HERE.parent.parent)}")
    none_count = sum(1 for r in results if not r["pt_set"])
    print(f"PT 확보: {len(results)-none_count}/{len(results)}개  /  수동 처리 필요: {none_count}개")


if __name__ == "__main__":
    main()
