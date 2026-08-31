"""
C세부1 — 커버리지 1차 측정 (§8: GPU 산정의 전제조건)

S3의 faers_drug 테이블에서 FAERS 자유기재 약물명을 뽑아 match.py로 정규화한 뒤
drug_ingredient_map(브랜드명/성분명)과 대조해 몇 %가 1단계(사전 조회)에서 잡히는지 잰다.

이 스크립트만 예외적으로 duckdb에 의존한다 — Iceberg 테이블을 직접 읽어야 해서
stdlib만으로는 안 됨(다른 drug_dict 스크립트는 전부 stdlib만 씀).

사전 준비:
  pip install duckdb
  aws configure  (S3 읽기 권한 있는 IAM 키)

실행:
  python3 coverage.py   (S3 전체 스캔이라 수 분 걸림)
"""
import duckdb

from match import load_dictionary, lookup

BUCKET = "boaz-1234-825494477740-ap-northeast-2-an"
FAERS_DRUG_TABLE = f"s3://{BUCKET}/iceberg_warehouse/stage_a_raw/faers_drug"


def fetch_drugname_counts() -> list[tuple[str, int]]:
    """FAERS drugname을 대문자+공백정리 후 집계.

    대문자/공백만 다른 표기("Aspirin"·"ASPIRIN"·" Aspirin ")를 각각 다른 고유명으로
    세면 분모가 부풀려진다 — 처음엔 이 정리 없이 재서 658,552개가 나왔는데, 정리하고
    나니 535,316개로 줄었고 이게 B단계 쪽에서 별도로 만든 dict_unique_drugs 테이블
    개수와 정확히 일치했다(데이터 차이가 아니라 집계 방식 차이였음을 서로 확인)."""
    con = duckdb.connect()
    con.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
    con.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain, REGION 'ap-northeast-2');")
    return con.execute(f"""
        SELECT upper(trim(drugname)) AS name, count(*) AS n_reports
        FROM iceberg_scan('{FAERS_DRUG_TABLE}')
        WHERE drugname IS NOT NULL AND trim(drugname) != ''
        GROUP BY 1
    """).fetchall()


def main():
    print("FAERS drugname 집계 중 (S3 Iceberg 전체 스캔 — 수 분 걸림)...", flush=True)
    rows = fetch_drugname_counts()
    print(f"고유 약물명: {len(rows):,}개", flush=True)

    products, ingredients = load_dictionary()
    print(f"사전: 브랜드 {len(products):,}개, 성분 {len(ingredients):,}개", flush=True)

    hit_unique = hit_reports = total_reports = 0
    miss = []
    for name, n in rows:
        total_reports += n
        if lookup(name, products, ingredients):
            hit_unique += 1
            hit_reports += n
        else:
            miss.append((name, n))

    print(f"\n[고유명 기준] {hit_unique:,} / {len(rows):,} = {hit_unique / len(rows) * 100:.2f}%")
    print(f"[신고건 기준] {hit_reports:,} / {total_reports:,} = {hit_reports / total_reports * 100:.2f}%")
    print(f"못 잡은 고유명(2·3단계 후보, GPU 산정 입력값): {len(miss):,}개")

    miss.sort(key=lambda x: -x[1])
    print("\n못 잡은 것 중 신고 건수 많은 상위 15개:")
    for name, n in miss[:15]:
        print(f"  {n:>8,}  {name}")


if __name__ == "__main__":
    main()
