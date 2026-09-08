# data/faers_not_load/, data/aers_not_load/ 안의 모든 zip을 순회하며 DEMO/DRUG/REAC 헤더를 검사합니다.
# load_faers_master.py의 harmonize_legacy_schema(), load_aers_master.py의 harmonize_aers_schema()가
# 이미 알고 처리하는 컬럼명이 아닌 게 하나라도 나오면 경고로 표시합니다 (조용히 null/drop되는 걸 방지).
# 헤더 한 줄만 읽고 바로 닫기 때문에 파일 전체를 읽지 않고, S3도 전혀 건드리지 않습니다.

import sys
import glob
import zipfile

# Windows 콘솔 기본 코드페이지(cp949 등)에서도 이모지 출력이 깨지지 않도록 강제 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# harmonize_legacy_schema()의 target_schemas + 리네임되는 옛 이름(gndr_cod, lot_nbr)까지 포함
FAERS_KNOWN_COLUMNS = {
    "DEMO": {
        'primaryid', 'caseid', 'caseversion', 'i_f_code', 'event_dt', 'mfr_dt',
        'init_fda_dt', 'fda_dt', 'rept_cod', 'auth_num', 'mfr_num', 'mfr_sndr',
        'lit_ref', 'age', 'age_cod', 'age_grp', 'sex', 'e_sub', 'wt', 'wt_cod',
        'rept_dt', 'to_mfr', 'occp_cod', 'reporter_country', 'occr_country',
        'gndr_cod',  # sex로 리네임되는 옛 이름
    },
    "DRUG": {
        'primaryid', 'caseid', 'drug_seq', 'role_cod', 'drugname', 'prod_ai',
        'val_vbm', 'route', 'dose_vbm', 'cum_dose_chr', 'cum_dose_unit', 'dechal',
        'rechal', 'lot_num', 'exp_dt', 'nda_num', 'dose_amt', 'dose_unit',
        'dose_form', 'dose_freq',
        'lot_nbr',  # lot_num으로 리네임되는 옛 이름
    },
    "REAC": {'primaryid', 'caseid', 'pt', 'drug_rec_act'},
}

# harmonize_aers_schema()의 target_schemas (AERS는 리네임 없음)
AERS_KNOWN_COLUMNS = {
    "DEMO": {
        'isr', 'case', 'i_f_cod', 'foll_seq', 'image', 'event_dt', 'mfr_dt', 'fda_dt',
        'rept_cod', 'mfr_num', 'mfr_sndr', 'age', 'age_cod', 'gndr_cod', 'e_sub', 'wt',
        'wt_cod', 'rept_dt', 'occp_cod', 'death_dt', 'to_mfr', 'confid', 'reporter_country',
    },
    "DRUG": {'isr', 'drug_seq', 'role_cod', 'drugname', 'val_vbm', 'route', 'dose_vbm',
              'dechal', 'rechal', 'lot_num', 'exp_dt', 'nda_num'},
    "REAC": {'isr', 'pt'},
}

# (스캔할 폴더, 파일 접두사, 알려진 컬럼 목록) — 필요하면 faers_load도 여기 추가해서 돌릴 수 있다.
TARGETS = [
    ("data/faers_not_load", "faers_*.zip", FAERS_KNOWN_COLUMNS),
    ("data/aers_not_load", "aers_*.zip", AERS_KNOWN_COLUMNS),
]


def clean_column(name):
    return name.replace('﻿', '').strip().lower()


def scan_zip(zip_path, known_columns):
    """zip 1개의 DEMO/DRUG/REAC 헤더를 검사. (file_type, raw_header, unknown_cols) 리스트 반환."""
    results = []
    with zipfile.ZipFile(zip_path, 'r') as z:
        file_list = z.namelist()
        for file_type in ("DEMO", "DRUG", "REAC"):
            target_file = [f for f in file_list if file_type in f.upper() and f.upper().endswith('.TXT')]
            if not target_file:
                results.append((file_type, None, None))
                continue

            target = target_file[0]
            with z.open(target) as f:
                header_line = f.readline().decode('utf-8', errors='replace').strip()
            raw_header = header_line.split('$')
            cleaned = [clean_column(c) for c in raw_header]
            unknown = [c for c in cleaned if c not in known_columns[file_type]]
            results.append((file_type, raw_header, unknown))
    return results


def main():
    total_files = 0
    total_anomalies = 0

    for folder, pattern, known_columns in TARGETS:
        zip_paths = sorted(glob.glob(f"{folder}/{pattern}"))
        print(f"\n{'=' * 70}\n📂 {folder} — {len(zip_paths)}개 zip 스캔\n{'=' * 70}")

        for zip_path in zip_paths:
            total_files += 1
            file_anomalies = []
            for file_type, raw_header, unknown in scan_zip(zip_path, known_columns):
                if raw_header is None:
                    file_anomalies.append(f"    ⚠️ {file_type} 파일이 zip 안에 없음")
                    continue
                if unknown:
                    file_anomalies.append(
                        f"    ❌ {file_type} ({len(raw_header)}개 컬럼) 미확인 컬럼: {unknown}"
                    )

            if file_anomalies:
                total_anomalies += 1
                print(f"\n🔍 [{zip_path}]")
                for line in file_anomalies:
                    print(line)
            else:
                print(f"✅ [{zip_path}] 이상 없음 — 모든 컬럼이 harmonize 로직에 포함됨")

    print(f"\n{'=' * 70}")
    if total_anomalies == 0:
        print(f"🎉 총 {total_files}개 zip 전부 이상 없음 — harmonize 함수가 모든 컬럼을 알고 있습니다.")
    else:
        print(f"⚠️ 총 {total_files}개 중 {total_anomalies}개 zip에서 미확인 컬럼 발견 — 위 목록 확인 필요.")


if __name__ == "__main__":
    main()
