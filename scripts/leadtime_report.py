"""v2 리드타임 리포트 (로컬) — EC2 first_detection.csv + gold 로 "조치보다 얼마나 앞서 떴나".

finish_pit.py(EC2) 가 뽑은 (ingredient,pt,first_q) 를 받아, gold 양성의
FDA 조치 분기와 비교해 리드타임을 낸다. 성분/반응 키 맞추기는 labelmap(실사전) 사용.

실행:
  python scripts/leadtime_report.py \
      --first-detection first_detection.csv \
      --signals fct_signal_scores.parquet \
      --drug-map drug_ingredient_map.csv \
      --reaction-dict reaction_dict_final.csv \
      [--out leadtime_rows.csv]
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from scoring.keys import make_key
from scoring.ground_truth import load_gold, terminal_pairs
from scoring.labelmap import (
    load_drug_map, load_reaction_map, make_ingredient_resolver,
    make_reaction_resolver, vocab_from_signals,
)
from scoring.pit import leadtime_from_first, summarize


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="v2 리드타임 리포트")
    ap.add_argument("--first-detection", required=True, help="EC2 산출 (ingredient,pt,first_q)")
    ap.add_argument("--signals", required=True, help="fct_signal_scores (vocab 추출용)")
    ap.add_argument("--gold", default=None)
    ap.add_argument("--drug-map", default=None)
    ap.add_argument("--reaction-dict", default=None)
    ap.add_argument("--out", default=None, help="per-key 리드타임 CSV")
    args = ap.parse_args()

    signals = pd.read_parquet(args.signals) if args.signals.endswith((".parquet", ".pq")) \
        else pd.read_csv(args.signals, encoding="utf-8-sig")
    ing_vocab, pt_vocab = vocab_from_signals(signals)
    ing_of = make_ingredient_resolver(
        ing_vocab, load_drug_map(args.drug_map) if args.drug_map else None)
    rx_of = make_reaction_resolver(
        pt_vocab, load_reaction_map(args.reaction_dict) if args.reaction_dict else None)

    gold = load_gold(args.gold) if args.gold else load_gold()
    pairs = terminal_pairs(gold)
    pos = pairs[pairs["label"] == "양성"]

    # 양성 키 → 조치 분기(선형). 같은 키 여러 pair면 가장 이른 조치.
    pos_asof: dict = {}
    for _, row in pos.iterrows():
        ing = ing_of(row["product"])
        if not ing:
            continue
        qidx = int(row["first_cohort"]) % 10          # terminal_pairs: year*10+q
        action = int(row["first_year"]) * 4 + (qidx - 1)   # 선형 분기
        for r in rx_of(row["signal"]):
            k = make_key(ing, r)
            pos_asof[k] = min(pos_asof.get(k, action), action)

    fd = pd.read_csv(args.first_detection, encoding="utf-8-sig")
    first_detected = {make_key(i, p): int(q)
                      for i, p, q in zip(fd["ingredient"], fd["pt"], fd["first_q"])}

    rows = leadtime_from_first(pos_asof, first_detected)
    s = summarize(rows)
    print(f"양성 키 {s['n_positives']:,}  |  최초탐지 있음 {s['detected_ever']:,}  |  "
          f"조치 이전 탐지 {s['detected_before_action']:,} "
          f"(탐지율 {s['detection_rate_before']:.1%})")
    print(f"리드타임(분기): 중앙값 {s['median_lead']}  최대 {s['max_lead']}")

    if args.out:
        pd.DataFrame([
            {"ingredient": r.key.ingredient, "reaction": r.key.reaction,
             "action_q": r.action_asof, "first_detected_q": r.first_detected,
             "detected_before": r.detected_before, "lead_quarters": r.lead}
            for r in rows
        ]).to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"per-key 저장 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
