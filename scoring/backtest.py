"""백테스트 드라이버 — fct_signal_scores 를 정답지로 채점한다 (v1~v5).

흐름 (계획서 노트 ③ + §4-7):
  1) signals(fct_signal_scores) 로드  — ingredient, pt, ic025, a (+ v3 피처)
  2) gold 정답지 → build_labelset(매퍼 주입) → positives/negatives/sealed
  3) build_ranking(signals, version) → 정렬된 SignalKey 순위표
  4) score(ranking, positives, universe_size=N) → Scorecard
  5) 성적표 출력 + JSON

실행 예:
  # v1 — 지금 바로 (사전 경로는 EC2 산출물)
  python -m scoring.backtest --signals fct_signal_scores.csv --version v1 \\
         --drug-map silver/drug_dict/drug_ingredient_map.csv \\
         --reaction-dict silver/reaction_dict/reaction_dict_final.csv --gate

  # 여러 버전 한 번에 (v3+ 는 signals 에 피처 컬럼 있어야 함)
  python -m scoring.backtest --signals signals_with_features.parquet \\
         --version v1 --version v2 --version v3 --out out.json

signals 는 EC2 에서 fct_signal_scores 를 CSV/Parquet 로 export 한 파일을 넘긴다
(로컬에서 S3 를 끌면 egress 발생 — 집계·export 는 EC2 에서). Recall 상한은
성적표의 coverage 로 먼저 확인할 것.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from scoring import score
from scoring.ground_truth import build_labelset, load_gold
from scoring.labelmap import (
    build_eval_labelset, load_drug_map, load_reaction_map, vocab_from_signals,
)
from scoring.mappers import load_ingredient_mapper, load_reaction_mapper
from scoring.ranking import VERSIONS, Gate, build_ranking


def load_signals(path: str | Path) -> pd.DataFrame:
    """fct_signal_scores export(.csv/.parquet) 로드."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"signals 파일 없음: {p}")
    if p.suffix.lower() in (".parquet", ".pq"):
        return pd.read_parquet(p)
    return pd.read_csv(p, encoding="utf-8-sig")


def _parse_ks(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in s.split(",") if x.strip())


def run(args: argparse.Namespace) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔 UTF-8
    except (AttributeError, ValueError):
        pass

    signals = load_signals(args.signals)
    print(f"signals 로드: {len(signals):,}행  ({args.signals})")

    gold = load_gold(args.gold) if args.gold else load_gold()
    if args.drug_map or args.reaction_dict:
        # vocab 인식 + 다중PT 확장 (실사전 주입 시). 순위표에서 vocab 추출.
        ing_vocab, pt_vocab = vocab_from_signals(signals)
        ls = build_eval_labelset(
            gold, ing_vocab=ing_vocab, pt_vocab=pt_vocab,
            drug_map=load_drug_map(args.drug_map) if args.drug_map else None,
            reaction_map=load_reaction_map(args.reaction_dict) if args.reaction_dict else None,
            exclude_class_level=not args.include_classes,
        )
    else:
        # 사전 없으면 얇은 항등 매퍼 (커버리지 낮음 — coverage 로 확인)
        ls = build_labelset(
            gold,
            ingredient_of=load_ingredient_mapper(args.drug_map),
            reaction_of=load_reaction_mapper(args.reaction_dict),
        )
    print("라벨셋:", ls.summary())

    gate = Gate.standard() if args.gate else None
    ks = _parse_ks(args.ks)

    results: dict[str, dict] = {}
    for version in args.version:
        print("\n" + "=" * 60)
        print(f"  [{version}] {VERSIONS[version].note}")
        print("=" * 60)
        rr = build_ranking(
            signals, version,
            positives=ls.positives, negatives=ls.negatives,
            gate=gate, bg_neg_ratio=args.bg_neg_ratio,
        )
        card = score(rr.ranking, ls.positives,
                    universe_size=rr.universe_size, ks=ks)
        print(card.render())
        results[version] = {"ranking_meta": rr.meta, "scorecard": card.to_dict()}

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {"cutoff_year": ls.cutoff_year,
                 "n_positives_total": len(ls.positives),
                 "versions": results},
                ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nJSON 저장: {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FAERS 신호 순위표 백테스트 (v1~v5)")
    ap.add_argument("--signals", required=True,
                    help="fct_signal_scores export (.csv/.parquet)")
    ap.add_argument("--version", action="append", choices=list(VERSIONS),
                    help="채점할 버전 (반복 가능). 기본 v1")
    ap.add_argument("--gold", default=None, help="정답지 CSV (기본: labelset_gold)")
    ap.add_argument("--drug-map", default=None, help="product→ingredient_norm CSV")
    ap.add_argument("--reaction-dict", default=None, help="signal→pt CSV")
    ap.add_argument("--gate", action="store_true",
                    help="표준 게이트 적용 (a≥3 AND IC025>0)")
    ap.add_argument("--include-classes", action="store_true",
                    help="약물 클래스 수준 정답지 행도 포함(기본: 제외, 성분단위 범위)")
    ap.add_argument("--ks", default="100,500,1000,5000", help="평가 K (쉼표)")
    ap.add_argument("--out", default=None, help="결과 JSON 경로")
    ap.add_argument("--bg-neg-ratio", type=float, default=0.0,
                    help="v3+ 학습에 배경 음성(라벨 없는 후보)을 양성 수×ratio 개 추가 (기본 0=안 씀)")
    args = ap.parse_args(argv)
    if not args.version:
        args.version = ["v1"]
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
