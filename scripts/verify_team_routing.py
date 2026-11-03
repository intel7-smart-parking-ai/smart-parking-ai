"""Verify actual Nginx routes keep team and demo data separate."""
import json
import os
from urllib.error import HTTPError
from urllib.request import urlopen

base = os.environ.get('PARKON_TEST_URL', 'http://127.0.0.1:8080')

def get(path):
    with urlopen(base + path, timeout=15) as response:
        return json.load(response)

team = get('/api/v1/parking-lots')
assert [lot['lot_id'] for lot in team['items']] == ['A', 'B', 'C']
assert sum(lot['total_spaces'] for lot in team['items']) == 12
assert get('/api/v1/vehicles/01/location')['car_id'] == '01'
assert get('/api/v1/vehicles/01/location')['session_id'] is None
try:
    get('/api/v1/vehicles/24/location')
    raise AssertionError('Missing vehicle must return 404')
except HTTPError as error:
    assert error.code == 404
    assert json.load(error)['error']['code'] == 'VEHICLE_NOT_FOUND'

demo = get('/demo-api/v1/parking-lots')
assert [lot['lot_id'] for lot in demo] == ['A', 'B', 'C', 'D']
assert sum(len(lot['spaces']) for lot in demo) == 50
assert get('/demo-api/v1/health')['billing_provider'] == 'server_fixture'
assert 'database' not in get('/api/v1/health')
print('PASS: team 12-space contract, leading-zero IDs, errors and demo 50-space routes remain isolated.')
