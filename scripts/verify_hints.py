import sys; sys.path.insert(0, '.')
from domains.crew.skills.structural_normalization import ConstraintSemanticMapper
m = ConstraintSemanticMapper()
tests = [
    ('duration', '야간사업 취침시간 4시간 확보', 240, 'minutes'),
    ('time_of_day', '야간사업의 출고시간 가급적 18:00 이후 / 최소 17:00 이후', 1080, 'minutes'),
    ('duration', '사업당 인정대기시간은 3시간', 180, 'minutes'),
    ('duration', '강차 후 일정 휴양시간(2~3시간) 확보 노력', 180, 'minutes'),
    ('duration', '회사 내 체류시간은 가급적 12시간 이내로 작성', 720, 'minutes'),
]
for n, c, v, u in tests:
    r = m.map_param(n, c, v, u)
    print(f'{c[:45]:47s} val={v:5} -> {r}')
