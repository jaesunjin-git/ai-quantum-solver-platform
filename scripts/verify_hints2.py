import sys; sys.path.insert(0, '.')
from domains.crew.skills.structural_normalization import ConstraintSemanticMapper
m = ConstraintSemanticMapper()
tests = [
    ('duration', '당일입고:30분, 익일출고:50분 포함 시 5시간 20분 확보 필요', 320, 'minutes'),
    ('duration', '야간사업 취침시간 4시간 확보', 240, 'minutes'),
    ('duration', '주간사업 식사와 휴양을 고려 전반사업과 후반사업으로 구분', 0, 'minutes'),
    ('duration', '회사 내 체류시간은 가급적 12시간 이내로 작성', 720, 'minutes'),
]
for n, c, v, u in tests:
    r = m.map_param(n, c, v, u)
    print(f'{c[:50]:52s} val={v:5} -> {r}')
