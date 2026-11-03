"""Read-only PM layout/identifier checks, safe while virtual cars move."""
import json,os
from urllib.request import urlopen
base=os.environ.get('PARKON_TEST_URL','http://127.0.0.1:8080')+'/demo-api/v1'
def get(path):
    with urlopen(base+path,timeout=15) as response:
        data=json.load(response)
        return data['items'] if isinstance(data,dict) and 'items' in data else data
lots=get('/parking-lots')
expected=[('A','개발원주차장','real','basic',12),('B','부산역주차장','real','angled',16),('C','북항주차장','virtual','loop',12),('D','신세계백화점주차장','virtual','mall',64)]
for lot,(key,name,kind,layout,total) in zip(lots,expected):
    assert (lot['lot_id'],lot['name'],lot['type'],lot['layout_type'])==(key,name,kind,layout)
    assert [s['space_id'] for s in lot['spaces']]==[f'{key}-{n:02}' for n in range(1,total+1)]
    assert all('bay_label' not in s and 'space_label' not in s for s in lot['spaces'])
    assert all(s['lot_id']==key for s in lot['spaces'])
    if key=='D':
        assert [s['space_id'] for s in lot['spaces'] if s['space_type']=='ev']==[f'D-{n:02}' for n in range(49,57)]
        assert [s['space_id'] for s in lot['spaces'] if s['space_type']=='accessible']==[f'D-{n:02}' for n in range(45,49)]
    print('PASS:',key,name,total,'canonical spaces')
for sticker in ('10','21'):
    result=get('/sticker-search?car_id='+sticker)
    assert result['space_id'] and result['status']=='active'
    assert 'space_label' not in result
print('PASS: active physical lookup with canonical space_id')
