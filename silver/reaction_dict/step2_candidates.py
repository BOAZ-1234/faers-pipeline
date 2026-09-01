"""
2단계: 1단계에서 못 맞춘 signal에 대해 openFDA에서 후보 PT + 건수 확보
- 핵심 단어를 openFDA에 검색
- PT명에 핵심 단어가 실제로 포함된 것만 후보로 남김 (renal impairment 오염 방지)
결과: signal별 후보 목록 → 3단계 LLM/사람 판정의 입력
"""
import csv
import re
import time
import urllib.request
import urllib.parse
import json
from pathlib import Path

HERE = Path(__file__).parent

# 검색에서 뺄 불용어 (관련성 없이 흔한 단어)
STOPWORDS = {
    "and", "or", "in", "with", "of", "the", "a", "to", "including",
    "certain", "patients", "product", "specifically", "leading",
    "increased", "risk", "an", "following", "due", "related", "that",
    "may", "contribute", "receiving", "non", "site", "specific",
}


def core_words(signal: str) -> list[str]:
    """signal에서 검색용 핵심 단어 추출"""
    # 괄호/특수문자 제거
    s = re.sub(r"\([^)]*\)", "", signal)
    s = re.sub(r"[^\w\s]", " ", s)
    words = [w.lower() for w in s.split() if w.lower() not in STOPWORDS and len(w) > 3]
    return words


def search_candidates(word: str) -> list[dict]:
    """openFDA에서 word로 검색, PT명에 word가 포함된 것만 반환"""
    url = (
        f"https://api.fda.gov/drug/event.json?"
        f"search=patient.reaction.reactionmeddrapt:{urllib.parse.quote(word)}"
        f"&count=patient.reaction.reactionmeddrapt.exact&limit=100"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []  # 결과 없음 — 정상
        raise RuntimeError(f"openFDA HTTP {e.code} for word={word!r}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"openFDA 연결 실패 for word={word!r}: {e}") from e
    out = []
    for item in data.get("results", []):
        if word.upper() in item["term"]:
            out.append({"pt": item["term"], "count": item["count"]})
    return out


def main():
    with open(HERE / "output" / "step1_normalized.csv") as f:
        rows = list(csv.DictReader(f))
    unmatched = [r["signal"] for r in rows if not r["matched_pt"] and r["signal"].strip() and r["signal"] != "\xa0"]

    results = []
    for signal in unmatched:
        words = core_words(signal)
        # 단어별 후보 모아서 중복 제거 (건수 큰 것 우선)
        candidates = {}
        for w in words:
            for c in search_candidates(w):
                if c["pt"] not in candidates or c["count"] > candidates[c["pt"]]:
                    candidates[c["pt"]] = c["count"]
            time.sleep(0.25)
        top = sorted(candidates.items(), key=lambda x: -x[1])[:20]

        results.append({
            "signal": signal,
            "core_words": " | ".join(words),
            "candidates": "; ".join(f"{pt}({cnt})" for pt, cnt in top),
            "n_candidates": len(top),
        })
        print(f"\n{signal}")
        print(f"  핵심어: {words}")
        for pt, cnt in top[:8]:
            print(f"    {cnt:>8,}  {pt}")

    out_path = HERE / "output" / "step2_candidates.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["signal", "core_words", "candidates", "n_candidates"])
        writer.writeheader()
        writer.writerows(results)

    found = sum(1 for r in results if r["n_candidates"] > 0)
    print(f"\n후보 확보: {found}/{len(results)}개  →  output/step2_candidates.csv")


if __name__ == "__main__":
    main()
