"""
bronze/load_faers_master.py의 harmonize_legacy_schema(), bronze/load_aers_master.py의
harmonize_aers_schema()가 실제 zip 파일 헤더 스캔 결과(2012Q4/2013Q1 FAERS,
2004Q1/2006Q1 AERS)와 맞게 동작하는지 검증. 전부 로컬 Spark로 돌며 S3는 건드리지 않는다.
"""

from conftest import load_module


def get_faers_module():
    return load_module("load_faers_master", "bronze/load_faers_master.py")


def get_aers_module():
    return load_module("load_aers_master", "bronze/load_aers_master.py")


def test_harmonize_legacy_demo_handles_bom_leading_space_and_new_columns(local_iceberg_spark):
    mod = get_faers_module()
    # 2012Q4 DEMO 실제 헤더: 'wt_cod' 다음 필드에 선행 공백(' rept_dt')이 있었다
    header = ['primaryid', 'caseid', 'caseversion', 'i_f_code', 'event_dt', 'mfr_dt',
              'init_fda_dt', 'fda_dt', 'rept_cod', 'mfr_num', 'mfr_sndr', 'age', 'age_cod',
              'gndr_cod', 'e_sub', 'wt', 'wt_cod', ' rept_dt', 'to_mfr', 'occp_cod',
              'reporter_country', 'occr_country', 'source_zip']
    row = [str(i) for i in range(len(header))]
    row[header.index('gndr_cod')] = 'M'
    row[header.index('source_zip')] = 'faers_ascii_2012q4.zip'
    df = local_iceberg_spark.createDataFrame([row], header)

    out = mod.harmonize_legacy_schema(df, "DEMO")

    target = ['primaryid', 'caseid', 'caseversion', 'i_f_code', 'event_dt', 'mfr_dt',
              'init_fda_dt', 'fda_dt', 'rept_cod', 'auth_num', 'mfr_num', 'mfr_sndr',
              'lit_ref', 'age', 'age_cod', 'age_grp', 'sex', 'e_sub', 'wt', 'wt_cod',
              'rept_dt', 'to_mfr', 'occp_cod', 'reporter_country', 'occr_country', 'source_zip']
    assert out.columns == target

    result = out.collect()[0].asDict()
    assert result['sex'] == 'M'  # gndr_cod -> sex 리네임
    assert result['auth_num'] is None  # 2012Q4엔 없던 신규 컬럼은 null
    assert result['lit_ref'] is None
    assert result['age_grp'] is None
    assert result['rept_dt'] == row[header.index(' rept_dt')]  # 선행 공백 제거되고 값은 보존
    assert result['source_zip'] == 'faers_ascii_2012q4.zip'  # 재시작 안전장치용 추적 컬럼도 보존


def test_harmonize_legacy_drug_handles_bom_prefix_and_lot_nbr_rename(local_iceberg_spark):
    mod = get_faers_module()
    # 2012Q4 DRUG 실제 헤더: BOM이 primaryid 앞에 붙어있었고 lot_num이 lot_nbr로 오타
    header = ['﻿primaryid', 'caseid', 'drug_seq', 'role_cod', 'drugname', 'val_vbm',
              'route', 'dose_vbm', 'cum_dose_chr', 'cum_dose_unit', 'dechal', 'rechal',
              'lot_nbr', 'exp_dt', 'nda_num', 'dose_amt', 'dose_unit', 'dose_form', 'dose_freq',
              'source_zip']
    row = [str(i) for i in range(len(header))]
    row[header.index('﻿primaryid')] = 'PID123'
    row[header.index('lot_nbr')] = 'LOT999'
    row[header.index('source_zip')] = 'faers_ascii_2012q4.zip'
    df = local_iceberg_spark.createDataFrame([row], header)

    out = mod.harmonize_legacy_schema(df, "DRUG")

    target = ['primaryid', 'caseid', 'drug_seq', 'role_cod', 'drugname', 'prod_ai',
              'val_vbm', 'route', 'dose_vbm', 'cum_dose_chr', 'cum_dose_unit', 'dechal',
              'rechal', 'lot_num', 'exp_dt', 'nda_num', 'dose_amt', 'dose_unit',
              'dose_form', 'dose_freq', 'source_zip']
    assert out.columns == target

    result = out.collect()[0].asDict()
    assert result['primaryid'] == 'PID123'  # BOM 제거되고 값 보존
    assert result['lot_num'] == 'LOT999'    # lot_nbr -> lot_num 리네임
    assert result['prod_ai'] is None        # 레거시엔 없던 컬럼


def test_harmonize_legacy_reac_fills_missing_drug_rec_act(local_iceberg_spark):
    mod = get_faers_module()
    header = ['primaryid', 'caseid', 'pt', 'source_zip']
    df = local_iceberg_spark.createDataFrame([['1', '2', 'HEADACHE', 'faers_ascii_2012q4.zip']], header)

    out = mod.harmonize_legacy_schema(df, "REAC")

    assert out.columns == ['primaryid', 'caseid', 'pt', 'drug_rec_act', 'source_zip']
    assert out.collect()[0].drug_rec_act is None


def test_harmonize_aers_demo_fills_missing_reporter_country_for_2004(local_iceberg_spark):
    mod = get_aers_module()
    # 2004Q1 AERS DEMO 실제 헤더: reporter_country가 아예 없음 (22개)
    header = ['ISR', 'CASE', 'I_F_COD', 'FOLL_SEQ', 'IMAGE', 'EVENT_DT', 'MFR_DT', 'FDA_DT',
              'REPT_COD', 'MFR_NUM', 'MFR_SNDR', 'AGE', 'AGE_COD', 'GNDR_COD', 'E_SUB', 'WT',
              'WT_COD', 'REPT_DT', 'OCCP_COD', 'DEATH_DT', 'TO_MFR', 'CONFID', 'source_zip']
    row = [str(i) for i in range(len(header))]
    row[-1] = 'aers_ascii_2004q1.zip'
    df = local_iceberg_spark.createDataFrame([row], header)

    out = mod.harmonize_aers_schema(df, "DEMO")

    target = ['isr', 'case', 'i_f_cod', 'foll_seq', 'image', 'event_dt', 'mfr_dt', 'fda_dt',
              'rept_cod', 'mfr_num', 'mfr_sndr', 'age', 'age_cod', 'gndr_cod', 'e_sub', 'wt',
              'wt_cod', 'rept_dt', 'occp_cod', 'death_dt', 'to_mfr', 'confid', 'reporter_country',
              'source_zip']
    assert out.columns == target
    assert out.collect()[0].reporter_country is None
    # gndr_cod는 AERS에선 sex로 리네임되지 않고 그대로 남아야 한다 (FAERS와 다른 스펙)
    assert 'gndr_cod' in out.columns and 'sex' not in out.columns


def test_harmonize_aers_demo_2006_passthrough_with_reporter_country(local_iceberg_spark):
    mod = get_aers_module()
    # 2006Q1엔 reporter_country가 이미 있음 (23개) -> null로 덮어써지면 안 된다
    header = ['ISR', 'CASE', 'I_F_COD', 'FOLL_SEQ', 'IMAGE', 'EVENT_DT', 'MFR_DT', 'FDA_DT',
              'REPT_COD', 'MFR_NUM', 'MFR_SNDR', 'AGE', 'AGE_COD', 'GNDR_COD', 'E_SUB', 'WT',
              'WT_COD', 'REPT_DT', 'OCCP_COD', 'DEATH_DT', 'TO_MFR', 'CONFID', 'REPORTER_COUNTRY']
    row = [str(i) for i in range(len(header))]
    row[-1] = 'US'
    df = local_iceberg_spark.createDataFrame([row], header)

    out = mod.harmonize_aers_schema(df, "DEMO")

    assert out.collect()[0].reporter_country == 'US'
