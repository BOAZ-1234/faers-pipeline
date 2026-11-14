-- SILVER-2 "펼치기" 그레인 — 신고(case) 1건의 약물 1개 × 반응 1개가 한 행.
-- role_cod 필터(PS/SS만)·caseversion 최신판 선택·중복 ingredient_set 처리는
-- 전부 GOLD 집계 시점에 적용한다 — 여기선 원본을 그대로 보존해, 나중에 필터
-- 기준을 바꿔 재실험하거나 시점(as_of)별로 다른 버전을 골라 쓸 수 있게 한다
-- (B단계 그레인 결정 1~4, action-plan.md 1장 참고).
--
-- A단계 Iceberg 원본(faers_drug × faers_reac를 primaryid로 묶어 "펼치기", faers_demo로
-- caseversion/fda_dt 보강) + C단계 stg_resolution을 조인한 실데이터. drugname→ingredient_set
-- 매칭은 stg_resolution.raw_drug_name과 직접 텍스트 매칭하지 않는다 — build_resolution.py가
-- entity_id 충돌 시 대표 표기 1개만 남겨서, 대표로 안 뽑힌 원본 표기는 직접 매칭하면 전부
-- 놓친다. 그래서 raw_name_entity_map.csv(전체 641,097개 원본 표기 → entity_id,
-- build_raw_entity_map.py가 entity_id_for(normalize_query(name))로 생성)를 거쳐 entity_id로
-- 조인한다. 명시적 cast로 contract가 선언한 data_type과 정확히 일치시킨다.
--
-- drug_seq(FAERS 원본의 primaryid 내 약물 순번)를 그대로 끌고 온다 — PR#65 리뷰에서
-- 나온 그레인 유니크 테스트 제안은 (caseid,primaryid,role_cod,ingredient_set,pt)로는
-- 성분 재매칭 중복·진짜 반복 신고(원본 drug_seq가 다른 행)를 가짜 위반으로 잡아낸다.
-- 그런데 실측해보니 drug_seq 혼자로도 부족하다 — 아주 드물게(617M행 중 4,677건) 같은
-- (primaryid,drug_seq,role_cod)인데 drugname 표기만 다른 두 원본 행이 있다(예: "ELAVIL"
-- vs "ELAVIL /00002202/"). ingredient_set(해석된 값)으로 다시 묶으면 PR#65가 거절했던
-- 바로 그 문제(다른 원본 표기가 같은 성분으로 묶여 가짜로 합쳐짐)가 재발하므로, 원본
-- drugname을 그대로 모델에 남겨서 (primaryid,drug_seq,role_cod,drugname,pt)를 진짜
-- 그레인 유니크 키로 쓴다(tests/assert_grain_unique.sql) — drug CTE의 DISTINCT 기준과
-- 정확히 같다.
--
-- drug/reac CTE에 DISTINCT를 건다 — 실측(primaryid=264551051)으로 확인한 사례: 같은
-- (primaryid,drug_seq,role_cod,drugname)이 103번 완전히 똑같이 반복되는 행이 원본에
-- 있다(enoxaparin 케이스와 다름 — 거긴 drug_seq가 전부 달라서 "서로 다른 원본 행"이었고,
-- 이건 drug_seq까지 같은 "구별 불가능한 복사본"). "원본 보존" 원칙은 구별 가능한 원본
-- 레코드를 보존하는 것이지 완전 중복의 복사본 개수까지 보존하는 게 아니라서, 이런
-- 바이트 단위 완전 중복만 걷어낸다 — 조인 전 단계에서 걸어서 fan-out이 GOLD까지
-- 번지지 않게 한다.
{{ config(materialized='table') }}

with drug as (
    select distinct primaryid, caseid, role_cod, drugname, drug_seq
    from iceberg_scan('s3://boaz-1234-825494477740-ap-northeast-2-an/iceberg_warehouse/stage_a_raw/faers_drug')
),
reac as (
    select distinct primaryid, pt
    from iceberg_scan('s3://boaz-1234-825494477740-ap-northeast-2-an/iceberg_warehouse/stage_a_raw/faers_reac')
),
demo as (
    select primaryid, caseversion, fda_dt
    from iceberg_scan('s3://boaz-1234-825494477740-ap-northeast-2-an/iceberg_warehouse/stage_a_raw/faers_demo')
),
entity_map as (
    select raw_drug_name, entity_id
    from read_csv_auto('../../silver/drug_dict/raw_name_entity_map.csv', all_varchar=true)
),
resolution as (
    select entity_id, resolved_ingredient_set, unii
    from read_csv_auto('../../silver/drug_dict/resolution_merged.csv', all_varchar=true)
)

select
    cast(d.caseid                      as varchar) as caseid,
    cast(d.primaryid                   as varchar) as primaryid,
    cast(d.drug_seq                    as integer) as drug_seq,
    cast(dm.caseversion                as integer) as caseversion,
    cast(dm.fda_dt                     as varchar) as fda_dt,
    cast(d.role_cod                    as varchar) as role_cod,
    cast(d.drugname                    as varchar) as drugname,
    cast(res.resolved_ingredient_set   as varchar) as ingredient_set,
    cast(res.unii                      as varchar) as unii,
    cast(r.pt                          as varchar) as pt
from drug d
join reac r on d.primaryid = r.primaryid
join demo dm on d.primaryid = dm.primaryid
left join entity_map m on upper(trim(d.drugname)) = m.raw_drug_name
left join resolution res on m.entity_id = res.entity_id
