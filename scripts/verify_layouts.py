"""Read-only checks for PDF layouts and scoped vehicle lookup."""
import json
import os
from urllib.error import HTTPError
from urllib.request import urlopen

base = os.environ.get('PARKON_TEST_URL', 'http://127.0.0.1:8080') + '/demo-api/v1'
def get(path):
    with urlopen(base + path, timeout=15) as response:
        data=json.load(response)
        return data['items'] if isinstance(data,dict) and 'items' in data else data

lots = get('/parking-lots')
expected = [('A', 'real', 'basic', 6, 1), ('B', 'real', 'angled', 8, 2),
            ('C', 'virtual', 'loop', 6, 1), ('D', 'virtual', 'l_shape', 5, 1)]
assert sum(len(lot['spaces']) for lot in lots) == 50
for lot, (key, kind, layout, bank, accessible) in zip(lots, expected):
    assert (lot['lot_id'], lot['type'], lot['layout_type']) == (key, kind, layout)
    assert len(lot['spaces']) == bank * 2
    assert [s['bay_label'] for s in lot['spaces']] == [f'{z}-{n:02d}' for z in 'AB' for n in range(1,bank+1)]
    assert sum(s['space_type'] == 'ev' for s in lot['spaces']) == 2
    assert sum(s['space_type'] == 'accessible' for s in lot['spaces']) == accessible
    print('PASS:', key, kind, layout, bank*2, 'spaces and local labels')

for sticker, lot_id in [('10','A'),('21','B')]:
    result = get('/sticker-search?car_id='+sticker)
    assert result['car_id'] == sticker and result['lot_id'] == lot_id and result['lookup_type'] == 'sticker'
    assert 'plate_number' not in result and result['space_label']
    assert result['fee']['authority'] == 'server_fixture'
for number, plate, lot_id in [('9012','56다9012','C'),('3456','78라3456','D'),('0022','90마0022','D')]:
    result = get('/vehicle-search?vehicle_number='+number)
    assert result['plate_number'] == plate and result['lot_id'] == lot_id and result['lookup_type'] == 'plate'
    assert result['space_label'] and result['fee']['authority'] == 'server_fixture'
for path in ['/sticker-search?car_id=24','/vehicle-search?vehicle_number=1234']:
    try:
        get(path)
        raise AssertionError('Lookup must stay scoped to physical/virtual lots')
    except HTTPError as error:
        assert error.code == 404
print('PASS: physical sticker and virtual full-plate lookup, fees and cross-scope exclusion')
