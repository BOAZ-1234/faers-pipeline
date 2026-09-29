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
from build_map import SALT_SUFFIXES, normalize_ingredient  # 사전 만들 때 쓴 것과 동일 로직

# prod_ai(FAERS에서 FDA가 직접 채운 성분 필드)에 성분명이 아니라 분류값이 들어있는 경우.
# 실측(2026-09-18): 회수 대상 140,638개 중 상위 분류값 7개가 신고 75.6만 건(6.75%)을 차지 —
# 그대로 받아들이면 "COSMETICS"가 성분처럼 취급되는 등 B단계 집계가 오염된다.
#
# PR#30 리뷰("prod_ai도 사전 대조 필요") 대응 실측(2026-09-22): 사전 대조를 추가하면
# 현재 회수분 134,419개 중 17,946개(13.35%)가 탈락하는데, 그중 대다수(TOZINAMERAN·
# ETANERCEPT-SZZS 등 백신/바이오시밀러 공식 INN명)는 오타가 아니라 우리 사전이 작아서
# 못 찾는 정답이었다 — 사전 대조는 채택하지 않고, 진짜 모호한 값(NOS류)만 추가로
# 걸러내는 쪽으로 결정. "MINERALS"·"VITAMIN B"도 같은 조사에서 발견된 범주값
# (554개 이름/58,900건, 19개 이름/18,465건)이라 같이 추가.
PROD_AI_EXCLUDE = {
    "UNSPECIFIED INGREDIENT", "COSMETICS", "VITAMINS", "DEVICE",
    "DIETARY SUPPLEMENT", "INVESTIGATIONAL PRODUCT", "HERBALS",
    "MINERALS", "VITAMIN B",
}

# "X NOS"(Not Otherwise Specified, 달리 명시 안 됨) 패턴 — 어떤 성분의 구체적 종류인지
# 특정하지 않은 값이라 exact-match 목록으로 일일이 나열할 수 없다. 실측: 622개 이름,
# 신고 199,190건이 여기 해당(INSULIN NOS·PROBIOTICS NOS·COVID-19 VACCINE NOS 등).
# 단어 경계(\b)를 써서 NOSCAPINE 같은 실제 성분명은 안 걸리게 한다.
_NOS_PATTERN = re.compile(r"\bNOS\b")

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


def resolve_prod_ai(prod_ai: str | None) -> str | None:
    """FAERS prod_ai(FDA가 직접 채운 성분 필드)를 1단계 폴백 입력으로 정규화한다.
    drugname으로 사전 조회가 실패했을 때만 쓴다 — prod_ai는 신뢰 가능한 소스라
    사전 대조 없이(exact-match 불필요) 정규화만 거쳐 바로 채택한다. 단, prod_ai도
    "\\"로 복합제를 구분하므로(예: "ABACAVIR SULFATE\\LAMIVUDINE") 그대로 재사용한다.
    분류값(COSMETICS 등, PROD_AI_EXCLUDE)만 들어있으면 못 찾은 것으로 처리한다.

    복합제("\\" 구분)는 부품마다 따로 걸러서, NOS/분류값인 부품만 빼고 나머지는 살린다
    (예: "AMINO ACIDS\\ELECTROLYTES NOS\\SOYBEAN OIL" → "electrolytes nos"만 제외).

    반환: §5-1 ingredient_set 형식(정렬·소문자·파이프) 문자열, 못 쓰면 None."""
    if not prod_ai or not prod_ai.strip():
        return None
    parts = split_backslash_parts(prod_ai) or [prod_ai]
    normed = {normalize_ingredient(p) for p in parts} - PROD_AI_EXCLUDE
    normed = sorted(v for v in normed if not _NOS_PATTERN.search(v))
    if not normed:
        return None
    return "|".join(p.lower() for p in normed)
