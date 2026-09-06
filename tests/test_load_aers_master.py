"""
bronze/load_aers_master.py의 classify_line() 검증.

실제 운영에서 발견된 버그: AERS 원본 데이터 행은 마지막 필드 뒤에 종결용 '$'가
하나 더 붙어있어서(헤더 줄에는 없음), 그냥 split('$')하면 항상 헤더보다 컬럼이
1개 많아져서 정상 행이 전부 격리 테이블로 새버렸다(35개 파일 전량 0건 적재).
"""

from conftest import load_module


def get_aers_module():
    return load_module("load_aers_master", "bronze/load_aers_master.py")


def test_classify_line_strips_trailing_terminator_dollar():
    mod = get_aers_module()
    header = ['ISR', 'CASE', 'I_F_COD', 'FOLL_SEQ', 'IMAGE', 'EVENT_DT', 'MFR_DT', 'FDA_DT',
              'REPT_COD', 'MFR_NUM', 'MFR_SNDR', 'AGE', 'AGE_COD', 'GNDR_COD', 'E_SUB', 'WT',
              'WT_COD', 'REPT_DT', 'OCCP_COD', 'DEATH_DT', 'TO_MFR', 'CONFID']
    # 실제 2004Q1 DEMO 파일에서 캡처한 실제 행 (끝에 종결 '$' 있음)
    line = (b'4204616$5657190$I$$4204616-7$20030815$20030918$20031006$EXP$DL2003174$'
            b'DANCO LABORATORIES, LLC$21$YR$F$N$$$20031002$MD$$$$\r\n')

    row, reject = mod.classify_line(line, header, "aers_ascii_2004q1.zip", "DEMO04Q1.TXT", "DEMO", 2)

    assert reject is None
    assert row is not None
    assert len(row) == len(header)
    assert row[0] == '4204616' and row[1] == '5657190'


def test_classify_line_still_quarantines_genuinely_short_rows():
    mod = get_aers_module()
    header = ['ISR', 'PT']
    # 정말로 컬럼이 부족한 행 (종결 '$' 떼도 여전히 개수 안 맞음)
    row, reject = mod.classify_line(b'123456\n', header, "f.zip", "REAC.txt", "REAC", 5)

    assert row is None
    assert reject == ["f.zip", "REAC.txt", "REAC", 5, 2, 1, "123456"]


def test_classify_line_handles_row_without_trailing_dollar_normally():
    mod = get_aers_module()
    header = ['ISR', 'PT']
    # 종결 '$'가 없는 정상 케이스도 그대로 잘 되어야 함
    row, reject = mod.classify_line(b'123456$HEADACHE\n', header, "f.zip", "REAC.txt", "REAC", 3)

    assert reject is None
    assert row == ['123456', 'HEADACHE']
