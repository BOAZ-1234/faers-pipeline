"""diana_golden.py 핵심 로직 self-check — 프레임워크 없이 assert만."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from build_map import normalize_ingredient
from diana_golden import filter_manual_curated, split_dev_test


def _pairs(*specs):
    return [{"raw_drug_name": n, "ingredient_norm": i, "unii": "", "source": "diana"} for n, i in specs]


def test_freq_filter():
    pairs = _pairs(("ASPIRIN", "ASPIRIN"), ("rareX", "FOO"))
    freq = {"ASPIRIN": 500, "RAREX": 10}
    kept = filter_manual_curated(pairs, freq, 200)
    assert [p["raw_drug_name"] for p in kept] == ["ASPIRIN"], kept
    # 빈도 없는 이름은 0으로 취급 → 제외
    assert filter_manual_curated(_pairs(("Unknown", "X")), {}, 200) == []


def test_split_no_name_leakage():
    # 같은 약 이름의 여러 성분 행이 dev/test에 갈리면 안 된다
    pairs = _pairs(*[(f"drug{i}", f"ing{i}") for i in range(100)])
    pairs += _pairs(("drug0", "ing0b"))  # drug0에 성분 2개
    dev, test = split_dev_test(pairs, seed=42, test_frac=0.2)
    dev_names = {p["raw_drug_name"] for p in dev}
    test_names = {p["raw_drug_name"] for p in test}
    assert dev_names.isdisjoint(test_names), "이름이 dev/test에 겹침 = leakage"
    assert len(dev) + len(test) == len(pairs)


def test_split_deterministic():
    pairs = _pairs(*[(f"d{i}", f"i{i}") for i in range(50)])
    a = split_dev_test(pairs, seed=42, test_frac=0.2)
    b = split_dev_test(pairs, seed=42, test_frac=0.2)
    assert a == b, "같은 seed인데 분할이 다름"


def test_ingredient_norm():
    assert normalize_ingredient("  carbidopa ") == "CARBIDOPA"
    assert normalize_ingredient("oxycodone hydrochloride") == "OXYCODONE"   # 염접미사 제거
    assert normalize_ingredient("oxycodone   hydrochloride") == "OXYCODONE"


if __name__ == "__main__":
    test_freq_filter()
    test_split_no_name_leakage()
    test_split_deterministic()
    test_ingredient_norm()
    print("모든 체크 통과")
