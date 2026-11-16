"""조인키 매퍼 — gold 정답지(product/signal 원문)를 순위표와 같은 키로 바꾼다.

계획서 노트 ③: 순위표와 정답지는 **같은 키**를 써야 한다. 순위표의 성분은 이미
이수연 `drug_ingredient_map` 를 통과한 `ingredient_norm`, 반응은 하경
`reaction_dict` 를 통과한 PT 다. 정답지의 product/signal 원문도 **같은 사전**으로
변환해야 조인된다.

이 모듈은 그 사전을 CSV 에서 읽어 `ingredient_of` / `reaction_of` 함수로 만든다.
사전 파일이 없으면(gitignore 로 로컬 미보유 — EC2 에서 생성) **항등 매퍼 + 경고**로
떨어져서, 최소한 "이름이 성분과 같은" gold 는 매칭되게 한다. 실제 매칭률은
`build_labelset` 결과의 coverage 로 반드시 확인할 것(= Recall 상한).

주의: gold `signal` 한 칸에 반응이 여러 개인 경우가 있다
("Torsades de pointes, hepatotoxicity, and blood dyscrasias"). 다중 PT 분해는
reaction_dict 책임 — 여기선 1:1 매퍼만 제공하고, 분해가 필요하면 reaction_dict
쪽에서 확장한다(현재 한계로 남겨둠).
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Callable

import pandas as pd

from scoring.keys import canonical

Mapper = Callable[[str], str]


def _dict_mapper(pairs: dict[str, str]) -> Mapper:
    """canonical(key) → value. 못 찾으면 원문 그대로(항등 fallback)."""
    table = {canonical(k): v for k, v in pairs.items()}

    def _map(s: str) -> str:
        return table.get(canonical(s), s)

    return _map


def load_mapper_csv(
    path: str | Path,
    key_col: str,
    val_col: str,
) -> Mapper:
    """CSV(key_col→val_col) → 매퍼. 파일 없으면 항등 + 경고."""
    p = Path(path)
    if not p.exists():
        warnings.warn(
            f"매퍼 사전 없음: {p} → 항등 매퍼로 진행(매칭률 하락). "
            "EC2 에서 사전을 생성하거나 경로를 지정하라.",
            stacklevel=2,
        )
        return lambda s: s
    df = pd.read_csv(p, encoding="utf-8-sig", dtype=str).fillna("")
    for c in (key_col, val_col):
        if c not in df.columns:
            raise KeyError(f"{p}: 컬럼 '{c}' 없음 (있는 컬럼: {list(df.columns)})")
    return _dict_mapper(dict(zip(df[key_col], df[val_col])))


def load_ingredient_mapper(
    path: str | Path | None = None,
    *,
    key_col: str = "product",
    val_col: str = "ingredient_norm",
) -> Mapper:
    """product/브랜드 → ingredient_norm. 이수연 drug_ingredient_map 계열.

    path 없으면 항등(이름이 성분과 같은 gold 만 매칭). 실제 운영 시 사전 경로 주입.
    """
    if path is None:
        warnings.warn(
            "ingredient 매퍼 경로 미지정 → 항등. gold product(브랜드)가 성분명과 "
            "다르면 매칭 안 됨. drug_ingredient_map CSV 경로를 넘겨라.",
            stacklevel=2,
        )
        return lambda s: s
    return load_mapper_csv(path, key_col, val_col)


def load_reaction_mapper(
    path: str | Path | None = None,
    *,
    key_col: str = "signal",
    val_col: str = "pt",
) -> Mapper:
    """gold signal → 반응 PT. 하경 reaction_dict 계열.

    path 없으면 항등(signal 표기가 PT 와 같을 때만 매칭). 다중-PT signal 분해는
    reaction_dict 확장 몫(모듈 docstring 참고).
    """
    if path is None:
        warnings.warn(
            "reaction 매퍼 경로 미지정 → 항등. gold signal 표기가 PT 와 다르면 "
            "매칭 안 됨. reaction_dict CSV 경로를 넘겨라.",
            stacklevel=2,
        )
        return lambda s: s
    return load_mapper_csv(path, key_col, val_col)
