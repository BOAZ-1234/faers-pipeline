import zipfile

zip_path = "data/faers_load/faers_ascii_2026Q1.zip"

print(f"🔍 [{zip_path}] 헤더 스캔 시작...\n")

with zipfile.ZipFile(zip_path, 'r') as z:
    for file_info in z.infolist():
        filename = file_info.filename.upper()
        
        # 텍스트 파일이면서 DEMO, DRUG, REAC 글자가 포함된 파일만 열어봅니다.
        if filename.endswith('.TXT') and any(keyword in filename for keyword in ['DEMO', 'DRUG', 'REAC']):
            with z.open(file_info) as f:
                # 무거운 파일 전체를 안 읽고, 딱 첫 줄(f.readline)만 읽고 바로 닫습니다!
                header_line = f.readline().decode('utf-8', errors='replace').strip()
                
                # $ 기호로 쪼개서 보기 좋게 리스트로 출력
                columns = header_line.split('$')
                
                print(f"📁 파일명: {filename}")
                print(f"📝 컬럼 리스트 ({len(columns)}개):")
                print(columns)
                print("-" * 50)