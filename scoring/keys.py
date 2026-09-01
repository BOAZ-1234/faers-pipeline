"""조인 키 — 순위표와 정답지를 잇는 단 하나의 규칙.

계획서 노트 ③: "우리 순위표와 정답지를 같은 키로 만든다."
성분/부작용 정규화는 상류(drug_dict, reaction_dict)에서 끝난다고 본다.
여기서는 그 결과물에 마지막으로 **동일한 얇은 규칙**만 걸어 완전일치를 판정한다.
이 canonical() 을 순위표·정답지 양쪽이 똑같이 통과해야만 키가 맞는다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_WS = re.compile(r"\s+")
_EDGE_PUNCT = re.compile(r"^[\s\.\,\;\:\-–—]+|[\s\.\,\;\:\-–—]+$")


def canonical(s: str) -> str:
    """조인용 최종 정규화 (얇게 유지).

    - 유니코드 NFKC 정규화 (전각/합자 흡수)
    - 앞뒤 공백·구두점 제거, 내부 연속 공백 1칸으로
    - 소문자화

    주의: 여기서 영국식 철자·어순·브랜드→성분 같은 **의미 정규화는 하지 않는다.**
    의미 정규화 산출물을 이미 정규화된 문자열로 받는다.
    """
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = _EDGE_PUNCT.sub("", s)
    s = _WS.sub(" ", s)
    return s.strip().lower()


@dataclass(frozen=True)
class SignalKey:
    """(성분, 부작용) 신호 한 쌍. 항상 canonical() 을 통과한 값으로 만든다."""

    ingredient: str
    reaction: str

    def __post_init__(self):
        # frozen dataclass 이므로 object.__setattr__ 로 정규화 값 강제
        object.__setattr__(self, "ingredient", canonical(self.ingredient))
        object.__setattr__(self, "reaction", canonical(self.reaction))

    def is_valid(self) -> bool:
        return bool(self.ingredient) and bool(self.reaction)

    def __str__(self) -> str:
        return f"{self.ingredient} :: {self.reaction}"


def make_key(ingredient: str, reaction: str) -> SignalKey:
    """(성분, 부작용) → SignalKey. 정규화는 SignalKey 내부에서 처리."""
    return SignalKey(ingredient, reaction)
