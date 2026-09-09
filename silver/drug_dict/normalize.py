"""
C단계 공통 정규화 함수 (캐스케이드 계약 회의 안건 D, 2026-09-08 확정)

Stage 0~3 전 단계가 이 함수 하나를 공유한다 — 단계마다 다른 정규화를 쓰면
조인이 조용히 깨진다는 게 회의에서 나온 원칙. 원래 C세부1(match.py)에서 커버리지
측정용으로 만든 버전을 그대로 베이스로 채택했다.

용량/제형 단어 목록은 §4-2에 "DURAGESIC-100 → 용량/제형 떼기 → DURAGESIC"라는
예시 하나만 있고 정확한 목록은 기획서에 없어 실제 FAERS drugname 표본을 보고
직접 채운 초안이다 — 검토 필요.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from build_map import SALT_SUFFIXES  # 사전 만들 때 쓴 염/수화물 접미사와 동일 목록

# 제형·투여경로·방출형태·약전 표기 등 — 끝에서부터 반복적으로 뗀다
DOSAGE_FORM_WORDS = {
    "TABLET", "TABLETS", "TAB", "TABS", "CAPSULE", "CAPSULES", "CAP", "CAPS",
    "INJECTION", "INJECTABLE", "SOLUTION", "SOLN", "CREAM", "PATCH", "GEL",
    "SYRUP", "SUSPENSION", "SPRAY", "OINTMENT", "LOTION", "POWDER",
    "DROPS", "LOZENGE", "SUPPOSITORY", "ORAL", "TOPICAL", "IV", "IM", "SC",
    "ER", "XR", "SR", "CR", "DR", "EC", "IR", "HFA", "USP", "NOS", "PF",
    "EXTENDED", "RELEASE", "DELAYED", "IMMEDIATE",
    "POUDRE", "POUR",  # 프랑스어 라벨(POUDRE POUR SOLUTION INJECTABLE)이 섞여 들어옴
    # 단위(공백으로 떨어진 경우: "500 MG")
    "MG", "MCG", "G", "KG", "ML", "L", "MEQ", "IU", "UNIT", "UNITS", "%",
}
_UNIT_ATTACHED = re.compile(r"^\d+(\.\d+)?(MG|MCG|G|KG|ML|L|MEQ|IU|UNIT|UNITS|%)$", re.I)
_PURE_NUM = re.compile(r"^\d+(\.\d+)?$")
_TRAILING_HYPHEN_NUM = re.compile(r"^(.*\S)-\d+(\.\d+)?$")
# NDC 코드 등 "/00002701/" 같은 숫자 코드 토큰 — 약물명이 아니라 부가 식별자라 뗀다
_CODE_TOKEN = re.compile(r"^/\d+/$")
_BRACKET = re.compile(r"\[([^\[\]]+)\]")


def normalize_query(name: str) -> str:
    """자유기재 약물명에서 용량/제형을 반복적으로 떼어 핵심 이름만 남긴다.
    "DURAGESIC-100" → "DURAGESIC", "LISINOPRIL TABLETS USP, 20MG" → "LISINOPRIL"."""
    s = name.strip().upper().rstrip(".").strip()
    changed = True
    while changed:
        changed = False
        s = s.rstrip(", ").strip()
        m = _TRAILING_HYPHEN_NUM.match(s)
        if m:
            s = m.group(1)
            changed = True
            continue
        tokens = s.split()
        if not tokens:
            break
        last = tokens[-1].rstrip(",")
        if last in DOSAGE_FORM_WORDS or _UNIT_ATTACHED.match(last) or _PURE_NUM.match(last) or _CODE_TOKEN.match(last):
            s = " ".join(tokens[:-1])
            changed = True
            continue
        # 사전 만들 때와 같은 염/수화물 접미사 (예: "AZITHROMYCIN ANHYDROUS" → "AZITHROMYCIN")
        if len(tokens) > 1 and last in SALT_SUFFIXES:
            s = " ".join(tokens[:-1])
            changed = True
    return s.strip()


def extract_bracket_alt(name: str) -> str | None:
    """"ALBUTEROL [SALBUTAMOL]" → "SALBUTAMOL". 원본이 괄호 안에 동의어를
    직접 적어준 경우라, 괄호 밖이 사전에 없어도 괄호 안이 바로 성분명인 경우가 있다."""
    m = _BRACKET.search(name)
    return m.group(1).strip() if m else None


def split_backslash_parts(name: str) -> list[str] | None:
    """"ACETAMINOPHEN\\OXYCODONE HYDROCHLORIDE" → ["ACETAMINOPHEN", "OXYCODONE HYDROCHLORIDE"].
    FAERS 자유기재에서 "\\"가 복합제 성분 구분자로 쓰이는 경우가 있다(§5-1 ingredient_set의
    "|"와 같은 역할, 원본 표기만 다름)."""
    if "\\" not in name:
        return None
    parts = [p.strip() for p in name.split("\\") if p.strip()]
    return parts if len(parts) > 1 else None
