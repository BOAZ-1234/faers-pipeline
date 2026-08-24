"""
1단계: 정규화 규칙 적용 후 openFDA에서 실제 FAERS 용어 확인
- 영국식 철자 변환
- 어순 조정 (Acute X → X acute 등)
결과: signal별로 matched_pt(FAERS 표준 용어), count, status 기록
"""
import csv
import re
import time
import urllib.request
import urllib.parse
import json
from pathlib import Path

HERE = Path(__file__).parent

# 미국식 → 영국식 철자 치환 (순서 중요: 긴 것 먼저)
SPELLING = [
    ("hemophagocytic",  "haemophagocytic"),
    ("hemorrhage",      "haemorrhage"),
    ("hemoglobin",      "haemoglobin"),
    ("tumor",           "tumour"),
    ("anaemia",         "anaemia"),    # 이미 영국식 — anemia보다 먼저
    ("anemia",          "anaemia"),
    ("leukaemia",       "leukaemia"),  # 이미 영국식
    ("leukemia",        "leukaemia"),
    ("hypoglycaemia",   "hypoglycaemia"),
    ("hypoglycemia",    "hypoglycaemia"),
    ("hypercalcaemia",  "hypercalcaemia"),
    ("hypercalcemia",   "hypercalcaemia"),
    ("hyperkalaemia",   "hyperkalaemia"),
    ("hyperkalemia",    "hyperkalaemia"),
    ("hypophosphataemia", "hypophosphataemia"),
    ("hypophosphatemia",  "hypophosphataemia"),
    ("hypogammaglobulinaemia", "hypogammaglobulinaemia"),
    ("hypogammaglobulinemia",  "hypogammaglobulinaemia"),
    ("glycosylated haemoglobin", "glycosylated haemoglobin"),
    ("glycosylated hemoglobin",  "glycosylated haemoglobin"),
    ("oedema",          "oedema"),     # 이미 영국식
    ("edema",           "oedema"),
    ("diarrhoea",       "diarrhoea"),  # 이미 영국식
    ("diarrhea",        "diarrhoea"),
    ("discolouration",  "discolouration"),
    ("discoloration",   "discolouration"),
    ("ischaemic",       "ischaemic"),  # 이미 영국식
    ("ischemic",        "ischaemic"),
    ("ischaemia",       "ischaemia"),
    ("ischemia",        "ischaemia"),
    ("behaviour",       "behaviour"),
    ("behavior",        "behaviour"),
    ("fetus",           "foetus"),
]

# 어순 뒤집기: "Acute X" → "X acute"
# MedDRA PT는 수식어가 뒤에 오는 게 관례
WORD_ORDER = [
    r"^Acute (.+)$",
    r"^Chronic (.+)$",
    r"^Subacute (.+)$",
]


def apply_spelling(term: str) -> str:
    t = term.lower()
    for us, uk in SPELLING:
        t = t.replace(us, uk)
    return t


def apply_word_order(term: str) -> list[str]:
    """원본 + 어순 변형 후보 반환"""
    candidates = [term]
    for pattern in WORD_ORDER:
        m = re.match(pattern, term, re.IGNORECASE)
        if m:
            modifier = re.search(r"^(\w+)", pattern.lstrip("^")).group(1)
            candidates.append(f"{m.group(1)} {modifier.lower()}")
    return candidates


def normalize(term: str) -> list[str]:
    """규칙 적용 후 후보 목록 반환 (중복 제거)"""
    term = term.replace("\xa0", "").replace("&nbsp;", "").strip()
    candidates = []
    for t in apply_word_order(term):
        candidates.append(apply_spelling(t))
        candidates.append(t.lower())
    return list(dict.fromkeys(candidates))


def query_openfda(term: str) -> tuple[str, int]:
    """openFDA에서 정확히 일치하는 PT 용어와 건수 반환. 없으면 ('', 0)"""
    encoded = urllib.parse.quote(f'"{term}"')
    url = f"https://api.fda.gov/drug/event.json?search=patient.reaction.reactionmeddrapt.exact:{encoded}&limit=1"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        count = data["meta"]["results"]["total"]
        return term, count
    except Exception:
        return "", 0


def main():
    with open(HERE / "output" / "step0_kept.csv") as f:
        signals = [r["signal"] for r in csv.DictReader(f)]

    results = []
    for signal in signals:
        candidates = normalize(signal)
        matched_pt, count, method = "", 0, "unmatched"

        for i, candidate in enumerate(candidates):
            pt, cnt = query_openfda(candidate)
            if cnt > 0:
                matched_pt = pt
                count = cnt
                method = "original" if i == 0 else "normalized"
                break
            time.sleep(0.25)  # rate limit

        results.append({
            "signal": signal,
            "matched_pt": matched_pt,
            "count": count,
            "method": method,
        })
        status = f"✓ {matched_pt} ({count:,})" if matched_pt else "✗ unmatched"
        print(f"{signal[:60]:<60}  {status}")

    out_path = HERE / "output" / "step1_normalized.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["signal", "matched_pt", "count", "method"])
        writer.writeheader()
        writer.writerows(results)

    matched = sum(1 for r in results if r["matched_pt"])
    print(f"\n매칭: {matched}/{len(results)}개  →  output/step1_normalized.csv")


if __name__ == "__main__":
    main()
