"""
[정답지 확보 v2] FDA 분기별 잠재 안전신호 보고서 다운로더 (2008~현재, 2018·2019 포함)

기존 scrape_fda_signals.py 의 한계
  - 진입점이 라이브 신호 페이지뿐 → 딥 아카이브(2018·2019 등)를 못 탐.
  - fda.gov 가 봇 차단(abuse-detection-apology)을 걸어 raw requests 가 통째로 404.
    → 2018·2019 가 정답지에서 통으로 빠졌음.

이 버전의 해법
  - FDA 봇 차단을 우회하려고 **Wayback Machine(web.archive.org)** 스냅샷으로 받는다.
    (아카이브 인덱스 자체가 이미 web.archive.org / archive-it.org 링크로 되어 있음)
  - 진입점 2개를 직접 판다:
      ① 아카이브 인덱스   → 2008~2021 전 분기 (2018·2019 4분기씩 포함)
      ② 현행 FAERS 랜딩   → 최근 분기(스냅샷 시점까지)
  - 마지막에 기존 out/fda_signals.csv 와 **합집합 병합**(--no-merge 로 끄기).
    이미 모아둔 2025·2026 등 최신 분기를 잃지 않기 위함.

실행:
    pip install requests pandas beautifulsoup4 lxml html5lib
    python bronze/fda_signals/download_fda_signals.py            # 병합 재생성
    python bronze/fda_signals/download_fda_signals.py --no-merge # 아카이브+랜딩만으로 새로

산출물(gitignore, out/):
    bronze/fda_signals/out/fda_signals.csv          약물 × 부작용 × 분기 × 조치 (정답지)
    bronze/fda_signals/out/fda_signals_raw.parquet  파싱한 원본 표 보존

계층 경계:
    bronze(여기, 원천 수집) → scoring/ground_truth.py(CSV→라벨셋) → scoring/scorer.py(채점)
"""

import re
import sys
import time
from io import StringIO
from pathlib import Path
from urllib.parse import urljoin

import requests
import pandas as pd
from bs4 import BeautifulSoup

# Windows 콘솔(cp949)이 ✓/★ 같은 글자에서 죽지 않도록 stdout을 utf-8로 고정
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
CSV = OUT / "fda_signals.csv"

UA = {"User-Agent": "Mozilla/5.0 (research; faers-pipeline scoring answer-key)"}
WB_AVAIL = "https://archive.org/wayback/available"

# 진입점 — 원본 fda.gov URL. resolve() 가 Wayback 스냅샷으로 바꿔 받는다.
SEEDS = [
    # ① 아카이브 인덱스: 2008~2021 분기 링크 전부 (2018·2019 포함)
    "https://www.fda.gov/drugs/fda-adverse-event-monitoring-system-aems/"
    "archived-quarterly-reports-new-safety-information-or-potential-signals-"
    "serious-risks-identified-fda",
    # ② 현행 FAERS 신호 랜딩: 최근 분기
    "https://www.fda.gov/drugs/questions-and-answers-fdas-adverse-event-reporting-"
    "system-faers/potential-signals-serious-risksnew-safety-information-identified-"
    "fda-adverse-event-reporting-system",
]

QUARTER_PAT = re.compile(
    r"(january|april|july|october)\s*[-–—]\s*"
    r"(march|june|september|december)\s*(\d{4})", re.I)


# ─────────────────────────────────────────────
# Wayback 우회 레이어
# ─────────────────────────────────────────────
_SNAP_RE = re.compile(r"/(?:web|\d+)/\d{10,}(?:id_|im_)?/(https?://.+)$", re.I)


def _is_webarchive(u: str) -> bool:
    return "web.archive.org" in u


def _orig(u: str) -> str:
    """Wayback/archive-it 스냅샷 URL → 원본 URL 추출."""
    m = _SNAP_RE.search(u)
    return m.group(1) if m else u


def _to_raw(snap_url: str) -> str:
    """web.archive.org 스냅샷 → 원본 HTML(id_ 모드). http는 https로 강제."""
    u = snap_url.replace("http://web.archive.org", "https://web.archive.org")
    if "id_/" in u:
        return u
    return u.replace("/http", "id_/http", 1)


def _avail(url: str) -> str | None:
    """라이브/원본 URL → 가장 가까운 web.archive.org 스냅샷 URL."""
    try:
        a = requests.get(WB_AVAIL, params={"url": url}, headers=UA, timeout=60).json()
        return a.get("archived_snapshots", {}).get("closest", {}).get("url")
    except Exception:
        return None


def resolve(url: str) -> str | None:
    """어떤 URL이든 → 받을 수 있는 web.archive.org raw(id_) 스냅샷 URL.

    web.archive.org 스냅샷은 그대로 raw 로. 그 외(라이브 URL, 또는
    archive-it 처럼 id_ 를 지원 안 하는 스냅샷)는 원본을 뽑아
    availability API 로 web.archive.org 스냅샷을 다시 받는다.
    """
    if _is_webarchive(url):
        return _to_raw(url)
    snap = _avail(_orig(url))
    return _to_raw(snap) if snap else None


def _get(target: str, tries: int = 3) -> str | None:
    for i in range(tries):
        try:
            r = requests.get(target, headers=UA, timeout=90)
            if r.status_code == 200 and "abuse-detection-apology" not in r.url:
                return r.text
        except Exception:
            pass
        time.sleep(1.5 * (i + 1))   # 연결거부(레이트리밋) 백오프
    return None


def fetch(url: str) -> tuple[str, str] | None:
    """(raw_html, 실제_받은_URL). 실패 시 None.

    1차: resolve() 가 준 스냅샷. 실패하면
    2차: 원본을 뽑아 availability 재조회 → 다른 스냅샷으로.
    """
    target = resolve(url)
    if target:
        html = _get(target)
        if html:
            return html, target
    snap = _avail(_orig(url))               # 2차 폴백
    if snap:
        target2 = _to_raw(snap)
        html = _get(target2)
        if html:
            return html, target2
    print(f"      fetch 실패(2차 폴백까지): …{url[-55:]}")
    return None


# ─────────────────────────────────────────────
# 1. 분기 보고서 링크 수집
# ─────────────────────────────────────────────
def find_report_links() -> list[tuple[str, str]]:
    """(정규화 분기라벨, URL). SEEDS(인덱스+랜딩)를 Wayback으로 훑는다."""
    found: dict[str, str] = {}
    for seed in SEEDS:
        got = fetch(seed)
        if not got:
            print(f"  ✗ 시드 접근 실패: …{seed[-45:]}")
            continue
        html, base = got
        soup = BeautifulSoup(html, "lxml")
        n0 = len(found)
        for a in soup.find_all("a", href=True):
            m = QUARTER_PAT.search(a.get_text(" ", strip=True))
            if not m:
                continue
            label = f"{m.group(1).capitalize()} - {m.group(2).capitalize()} {m.group(3)}"
            url = urljoin(base, a["href"])
            found.setdefault(label, url)   # 먼저 잡힌(인덱스) 링크 우선
        print(f"  ✓ 시드 …{seed[-40:]:40s} → +{len(found)-n0} (누적 {len(found)})")
        time.sleep(0.4)
    # 연도-분기 순 정렬
    order = {"January": 1, "April": 2, "July": 3, "October": 4}
    return sorted(found.items(),
                  key=lambda kv: (kv[0].split()[-1], order.get(kv[0].split()[0], 9)))


# ─────────────────────────────────────────────
# 2. 표 파싱
# ─────────────────────────────────────────────
def norm_cols(cols):
    out = []
    for c in cols:
        c = re.sub(r"\s+", " ", str(c)).strip().lower()
        if re.search(r"product|drug|biolog|active ingredient|name", c):
            out.append("product")
        elif re.search(r"signal|risk|adverse|reaction|safety information", c):
            out.append("signal")
        elif re.search(r"additional|information|action|status", c):
            out.append("info")
        else:
            out.append(c[:30])
    return out


def parse_report(url: str, label: str) -> pd.DataFrame:
    got = fetch(url)
    if not got:
        print(f"    ✗ {label}: 접근 실패")
        return pd.DataFrame()
    html, _ = got
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        tables = []
    if not tables:
        return pd.DataFrame()

    t = max(tables, key=len).copy()          # 가장 큰 표 채택
    t.columns = norm_cols(t.columns)
    if "product" not in t.columns or "signal" not in t.columns:
        return pd.DataFrame()

    t = t[[c for c in ("product", "signal", "info") if c in t.columns]].copy()
    m = QUARTER_PAT.search(label)
    t["quarter_label"] = label
    t["source_url"] = url
    t["year"] = int(m.group(3)) if m else None
    t["q_start"] = m.group(1).capitalize() if m else None
    return t


# ─────────────────────────────────────────────
# 3. 정제 — 한 셀에 여러 약물/신호면 분리
# ─────────────────────────────────────────────
def explode_pairs(df: pd.DataFrame) -> pd.DataFrame:
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
                rows.append({
                    "product": p, "signal": s,
                    "info": str(r.get("info", ""))[:300],
                    "quarter_label": r["quarter_label"],
                    "year": r["year"], "q_start": r["q_start"],
                    "source_url": r["source_url"],
                })
    return pd.DataFrame(rows).drop_duplicates(
        subset=["product", "signal", "quarter_label"])


# ─────────────────────────────────────────────
def main():
    merge = "--no-merge" not in sys.argv

    print("[1/3] 분기 보고서 링크 탐색 (Wayback 우회)")
    links = find_report_links()
    print(f"  총 {len(links)}개 분기")
    yrs = sorted({l.split()[-1] for l, _ in links})
    print(f"  연도: {', '.join(yrs)}")

    print("\n[2/3] 표 파싱")
    frames = []
    for label, url in links:
        t = parse_report(url, label)
        flag = "" if len(t) else "  ⚠ 표없음/PDF"
        print(f"  {label:24s} {len(t):>4}행{flag}")
        if len(t):
            frames.append(t)
        time.sleep(0.5)

    if not frames:
        print("\n  ❌ 표 파싱 0건. Wayback 스냅샷 상태 확인 필요")
        return

    raw = pd.concat(frames, ignore_index=True)
    raw.to_parquet(OUT / "fda_signals_raw.parquet", index=False)
    df = explode_pairs(raw)

    print("\n[3/3] 병합/저장")
    if merge and CSV.exists():
        old = pd.read_csv(CSV)
        before = len(df)
        df = (pd.concat([old, df], ignore_index=True)
                .drop_duplicates(subset=["product", "signal", "quarter_label"]))
        print(f"  기존 {len(old):,} + 신규 {before:,} → 합집합 {len(df):,}")
    df = df.sort_values(["year", "q_start", "product"]).reset_index(drop=True)
    df.to_csv(CSV, index=False, encoding="utf-8-sig")

    print(f"\n{'='*56}")
    print(f"  약물×신호 쌍   {len(df):,}   ★  → {CSV}")
    print(f"  고유 약물      {df['product'].nunique():,}")
    print(f"  고유 신호      {df['signal'].nunique():,}")
    print(f"  분기 수        {df['quarter_label'].nunique()}")
    print(f"  연도 범위      {df['year'].min():.0f} ~ {df['year'].max():.0f}")
    print("=" * 56)
    print("\n  연도별 쌍:")
    print(df.groupby("year").size().to_string())


if __name__ == "__main__":
    main()
