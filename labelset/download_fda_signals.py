"""[커버리지 확보 v2] FDA 분기별 잠재 안전신호 보고서 다운로더 (2008~현재, 2018·2019 포함).

기존 scrape_fda_signals.py 를 한 겹 더 감싼다. 각 보고서 URL을 **web.archive.org 스냅샷**으로
해소(resolve)해 받는다. archive-it(2018·2019 등) 스냅샷도 raw(id_) 형태로 원문 표를 얻는다.

⚠ 라이브 fda.gov 자체는 **사라진 게 아니라 봇 차단(abuse-detection)** 이라 requests 로는 404다
(apology_objects/abuse-detection). 아카이브 스냅샷은 이 코드로 되지만, 라이브 fda.gov 신규
분기는 브라우저(Claude in Chrome)로 표를 추출해야 한다(README 참고).

전략:
    resolve(url) → web.archive.org raw(id_) 스냅샷 URL
      · 이미 web.archive 계열이면 그대로 raw 변환
      · 아니면 원본 URL을 availability API로 조회해 가장 가까운 스냅샷을 raw 변환
    fetch(url)   → (raw_html, 실제_받은_URL). 1차 resolve 실패 시 원본 재조회로 2차 폴백.

주의: 이 파일은 삭제됐던 것을 pyc(download_fda_signals.cpython-311.pyc)+scrape_fda_signals.py
      기반으로 복원한 것. 원문 전체 텍스트는 out/fda_signals_raw.parquet 에 보존된다
      (explode 단계에서 CSV info 는 300자로 절단되므로, 원문은 parquet 기준).

실행:
    pip install requests pandas beautifulsoup4 lxml html5lib
    python download_fda_signals.py

산출물:
    out/fda_signals.csv          약물 × 부작용 × 분기 × info(300자, 하위호환) + info_full(무절단)
    out/fda_signals_raw.parquet  원본 표 보존 (info 전체 텍스트)
"""

import re
import time
from io import StringIO
from pathlib import Path
from urllib.parse import urljoin

import requests
import pandas as pd
from bs4 import BeautifulSoup

OUT = Path(__file__).resolve().parent / "out"; OUT.mkdir(exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (research)"}
WB_AVAIL = "https://archive.org/wayback/available"

# web.archive / archive-it 스냅샷 URL 에서 원본 URL 추출: /web/<ts>[id_|im_]/<원본>
_SNAP_RE = re.compile(r"/(?:web|\d+)/\d{10,}(?:id_|im_)?/(https?://.+)$")

SEEDS = [
    "https://www.fda.gov/drugs/questions-and-answers-fdas-adverse-event-reporting-system-faers/"
    "potential-signals-serious-risksnew-safety-information-identified-fda-adverse-event-reporting-system",
    "https://www.fda.gov/drugs/fda-adverse-event-monitoring-system-aems/"
    "archived-quarterly-reports-new-safety-information-or-potential-signals-serious-risks",
]

QUARTER_PAT = re.compile(
    r"(january|april|july|october)\s*[-–—]\s*(march|june|september|december)\s*(\d{4})", re.I)


# ─────────────────────────────────────────────
# web.archive 해소 계층
# ─────────────────────────────────────────────
def _is_webarchive(u: str) -> bool:
    return "web.archive.org" in u


def _is_archived(u: str) -> bool:
    """이미 아카이브 스냅샷 URL이면 True (web.archive / archive-it / wayback)."""
    return _is_webarchive(u) or "archive-it.org" in u or "wayback" in u


def _to_raw(snap_url: str) -> str:
    """web.archive.org 스냅샷 → 원문 HTML(id_ 형). http→https 정규화."""
    snap_url = snap_url.replace("http://web.archive.org", "https://web.archive.org")
    if "id_/" not in snap_url:
        snap_url = snap_url.replace("/http", "id_/http", 1)
    return snap_url


def _orig(url: str) -> str:
    """스냅샷 URL이면 감싼 원본 URL만 뽑고, 아니면 그대로."""
    m = _SNAP_RE.search(url)
    return m.group(1) if m else url


def _avail(url: str):
    """원본 URL → 가장 가까운 web.archive.org 스냅샷 URL (없으면 None)."""
    try:
        j = requests.get(WB_AVAIL, params={"url": url}, headers=UA, timeout=60).json()
        snap = j.get("archived_snapshots", {}).get("closest", {})
        return snap.get("url") if snap.get("available") else None
    except Exception:
        return None


def resolve(url: str):
    """어떤 URL이든 받을 수 있는 web.archive.org raw(id_) 스냅샷 URL로."""
    if _is_webarchive(url):
        return _to_raw(url)
    if _is_archived(url):        # archive-it/wayback: 이미 스냅샷 → 직접
        return url
    snap = _avail(_orig(url))
    return _to_raw(snap) if snap else None


def _get(url: str):
    r = requests.get(url, headers=UA, timeout=90)
    r.raise_for_status()
    return r.text


def fetch(url: str):
    """(raw_html, 실제_받은_URL). 실패 시 None.

    1차: resolve() 로 한 방에. 실패하면
    2차: 원본만 뽑아 availability 재조회 → 다른 스냅샷으로.
    """
    snap = resolve(url)
    if snap:
        try:
            return _get(snap), snap
        except Exception:
            pass
    snap2 = _avail(_orig(url))
    if snap2:
        try:
            raw = _to_raw(snap2)
            return _get(raw), raw
        except Exception:
            pass
    print(f"      fetch 실패(2차 폴백도): {url[-55:]}")
    return None


# ─────────────────────────────────────────────
# 링크 수집 · 표 파싱 · 정제 (scrape_fda_signals 와 동일, get→fetch)
# ─────────────────────────────────────────────
def find_report_links():
    """(분기라벨, URL) 목록. SEEDS(인덱스+아카이브)를 Wayback으로 받는다."""
    found, seen = {}, set()
    queue = list(SEEDS)
    while queue:
        page = queue.pop(0)
        if page in seen:
            continue
        seen.add(page)
        got = fetch(page)
        if not got:
            continue
        html, _ = got
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            txt = a.get_text(" ", strip=True)
            url = urljoin(page, a["href"])
            if QUARTER_PAT.search(txt):
                found[url] = txt
            elif re.search(r"potential signal|archived quarterly|안전신호", txt, re.I) \
                    and url not in seen:
                queue.append(url)
        print(f"  탐색 {page.split('/')[-1][:50]:50s} → 누적 {len(found)}개")
        time.sleep(0.4)
    return sorted(found.items(), key=lambda x: x[1])


def norm_cols(cols):
    out = []
    for c in cols:
        c = re.sub(r"\s+", " ", str(c)).strip().lower()
        if re.search(r"product|drug|biolog|name", c):     out.append("product")
        elif re.search(r"signal|risk|adverse|reaction", c): out.append("signal")
        elif re.search(r"additional|information|action|status", c): out.append("info")
        else: out.append(c[:30])
    return out


def parse_report(url, label) -> pd.DataFrame:
    got = fetch(url)
    if not got:
        return pd.DataFrame()
    html, real = got
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        tables = []
    if not tables:
        return pd.DataFrame()
    t = max(tables, key=len).copy()
    t.columns = norm_cols(t.columns)
    if "product" not in t.columns or "signal" not in t.columns:
        return pd.DataFrame()
    t = t[[c for c in ("product", "signal", "info") if c in t.columns]].copy()
    t["quarter_label"] = label
    t["source_url"] = url
    m = QUARTER_PAT.search(label)
    t["year"] = int(m.group(3)) if m else None
    t["q_start"] = m.group(1).capitalize() if m else None
    return t


def explode_pairs(df: pd.DataFrame) -> pd.DataFrame:
    """한 셀에 약물/신호가 여러 개면 분리. info 는 CSV용 300자 절단(원문은 parquet)."""
    rows = []
    for _, r in df.iterrows():
        prods = re.split(r"[;\n]|(?<=[a-z])\s*,\s*(?=[A-Z])", str(r["product"]))
        sigs = re.split(r"[;\n]", str(r.get("signal", "")))
        for p in prods:
            p = re.sub(r"\s+", " ", p).strip(" ,.")
            if len(p) < 2 or p.lower() in ("nan", "none"):
                continue
            for s in sigs:
                s = re.sub(r"\s+", " ", s).strip(" ,.")
                if len(s) < 3 or s.lower() in ("nan", "none"):
                    continue
                info_full = re.sub(r"\s+", " ", str(r.get("info", ""))).strip()
                rows.append({
                    "product": p, "signal": s,
                    "info": info_full[:300],      # 하위호환(구 컬럼)
                    "info_full": info_full,        # 절단 안 된 전체 원문
                    "quarter_label": r["quarter_label"],
                    "year": r["year"], "q_start": r["q_start"],
                    "source_url": r["source_url"],
                })
    return pd.DataFrame(rows).drop_duplicates(subset=["product", "signal", "quarter_label"])


def main():
    print("[1/3] 분기 보고서 링크 탐색 (web.archive 경유)")
    links = find_report_links()
    print(f"  총 {len(links)}개")
    if not links:
        print("  ❌ 링크 0개 — SEEDS 스냅샷 접근 실패")
        return

    print("\n[2/3] 표 파싱")
    frames = []
    for u, l in links:
        t = parse_report(u, l)
        print(f"  {l[:45]:45s} {len(t):>4}행")
        if len(t):
            frames.append(t)
        time.sleep(0.4)
    if not frames:
        print("  ❌ 표 파싱 0건")
        return

    raw = pd.concat(frames, ignore_index=True)
    raw.to_parquet(OUT / "fda_signals_raw.parquet", index=False)  # 원문 전체 텍스트 보존
    df = explode_pairs(raw)
    df.to_csv(OUT / "fda_signals.csv", index=False, encoding="utf-8-sig")

    print("\n[3/3] 정제 완료")
    print(f"  원본 행     {len(raw):,}")
    print(f"  약물×신호   {len(df):,}")
    print(f"  보고서 수   {df['quarter_label'].nunique()}")
    print(f"  연도 범위   {df['year'].min():.0f} ~ {df['year'].max():.0f}")


if __name__ == "__main__":
    main()
