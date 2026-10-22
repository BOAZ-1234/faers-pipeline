-- 조인키 형식 계약: 복합제 성분셋은 알파벳 정렬돼 있어야 한다 — 순서가 뒤바뀌면
-- 같은 약이 GOLD 집계에서 다른 키로 쪼개진다(assert_ingredient_set_sorted와 동일 원칙).
--
-- stg_resolution과 달리 이 테이블은 entity_id 1행=1성분셋이 아니다 — 같은
-- primaryid 안에 서로 다른 ingredient_set이 여러 번 나올 수 있다(예: 신고 한 건에
-- 성분이 다른 약이 2개). 그래서 (caseid, primaryid)로 묶으면 같은 그룹 안의 다른
-- ingredient_set 값이 any_value에 가려 검사가 누락된다 — 대신 ingredient_set
-- 값 자체(distinct)로 검사한다.
with distinct_sets as (
    select distinct ingredient_set as s
    from {{ ref('fct_case_drug_reaction') }}
    where ingredient_set like '%|%'
),
resorted as (
    select
        s,
        string_agg(part, '|' order by part) as sorted_form
    from distinct_sets, unnest(string_split(s, '|')) as t(part)
    group by s
)
select s as ingredient_set, sorted_form
from resorted
where s <> sorted_form
