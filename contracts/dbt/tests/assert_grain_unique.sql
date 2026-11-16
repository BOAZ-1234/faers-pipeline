-- PR#65 리뷰 3번: fct_case_drug_reaction의 그레인(SILVER-2, "약물 1개×반응 1개=1행")이
-- 실제로 지켜지는지 보는 테스트. (caseid,primaryid,role_cod,ingredient_set,pt)로 보면
-- 성분 재매칭 중복(§6)과 원본에 실제로 반복되는 drug_seq 행(0.64% 케이스, 실측 검증됨)을
-- 전부 "위반"으로 잘못 잡아낸다. drug_seq는 faers_drug 원본의 primaryid 내 약물 순번이지만
-- 실측 결과 drug_seq 혼자로는 "약물 1개"를 완전히 식별하지 못한다 — 아주 드물게(617M행 중
-- 4,677건, 0.0008%) 같은 (primaryid,drug_seq,role_cod)인데 drugname 표기만 다른 두 원본
-- 행이 있다(예: "ELAVIL" vs "ELAVIL /00002202/", "MIDAZOLAM" vs "MIDAZOLAM HYDROCHLORIDE.").
-- ingredient_set(해석된 값)으로 묶으면 PR#65가 거절했던 문제(다른 원본 표기가 같은 성분으로
-- 묶여 가짜로 합쳐짐)가 재발하므로 원본 drugname을 그대로 쓴다 — (primaryid,drug_seq,
-- role_cod,drugname,pt)가 drug CTE의 DISTINCT 기준과 정확히 같은, 진짜 그레인 유니크 키다.
-- 이게 조인으로 깨지면 그건 진짜 조인 버그다.
select primaryid, drug_seq, role_cod, drugname, pt, count(*) as n
from {{ ref('fct_case_drug_reaction') }}
group by primaryid, drug_seq, role_cod, drugname, pt
having count(*) > 1
