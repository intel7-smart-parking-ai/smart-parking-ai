from contextlib import closing
from pathlib import Path
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.runtime import create_runtime_app
from backend.app.simulation.mock_data import SPACES
from backend.app.simulation.vehicle_assignment import choose_entry_type

TOKEN = 'simulator-dedicated-test'


def wait_for(read, predicate, timeout=18):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(.2)
    raise AssertionError(value)


def spaces(client, lot):
    return client.get(f'/api/v1/parking-lots/{lot}/spaces').json()['items']


def command(client, lot, action, **body):
    response = client.post(f'/api/v1/simulation/{lot}/{action}', json=body)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('lot', ['C', 'D'])
def test_auto_assignment_populates_every_catalog_type_without_cross_parking(lot):
    rows = [dict(space_id=s.space_id, space_type=s.space_type, status='empty')
            for s in SPACES if s.lot_id == lot]
    targets = {r['space_id']: {'occupied': False} for r in rows}
    types = []
    while available := [r for r in rows if r['status'] == 'empty']:
        kind = choose_entry_type(rows, targets, available)
        row = next(r for r in available if r['space_type'] == kind)
        types.append(kind)
        row['status'] = 'occupied'
        targets[row['space_id']]['occupied'] = True
    assert types[:3] == ['general', 'ev', 'accessible']
    assert len(types) == 64
    for kind in ('general', 'ev', 'accessible'):
        assert types.count(kind) == sum(r['space_type'] == kind for r in rows)


@pytest.mark.parametrize('lot', ['C', 'D'])
def test_manual_types_persist_and_clear_preserves_sessions_and_payment(tmp_path, lot):
    database = tmp_path / 'typed.sqlite3'
    def app():
        return create_runtime_app(token=TOKEN, database=database, mode='demo')
    numbers = {'general': '7620', 'ev': '9001', 'accessible': '9002'}
    session_ids = []
    with TestClient(app()) as client:
        for kind, number in numbers.items():
            # Legacy registered numbers infer their type without new request fields.
            result = command(client, lot, 'entry', vehicle_number=number)
            row = next(r for r in spaces(client, lot) if r['space_id'] == result['space_id'])
            assert row['vehicle_type'] == row['space_type'] == kind
            session_ids.append(row['session_id'])
        response = client.post(f'/api/v1/simulation/{lot}/entry', json={
            'vehicle_type': 'general', 'space_id': next(s.space_id for s in SPACES if s.lot_id == lot and s.space_type == 'ev')})
        assert response.status_code == 409
        assert client.post(f'/api/v1/simulation/{lot}/entry', json={'vehicle_number': '9001', 'vehicle_type': 'accessible'}).status_code == 409
        other = 'D' if lot == 'C' else 'C'
        assert client.post(f'/api/v1/simulation/{other}/entry', json={'vehicle_number': '9001'}).status_code == 409
        fee = client.get(f'/api/v1/sessions/{session_ids[0]}/fee').json()
        paid = client.post('/api/v1/mock-payments', json={
            'session_id': session_ids[0], 'quote_id': fee['quote_id'],
            'idempotency_key': 'before-clear', 'payment_method': 'MOCK_CARD'})
        assert paid.status_code == 200, paid.text
        payment_id = paid.json()['payment_id']
    with TestClient(app()) as client:
        rows = wait_for(lambda: spaces(client, lot), lambda rows: sum(r['status'] == 'occupied' for r in rows) == 3)
        assert {r['vehicle_type'] for r in rows if r['status'] == 'occupied'} == set(numbers)
        assert {r['session_id'] for r in rows if r['status'] == 'occupied'} == set(session_ids)
        command(client, lot, 'start', interval_seconds=60)
        assert client.post(f'/api/v1/simulation/{lot}/clear').status_code == 409
        command(client, lot, 'stop')
        cleared = command(client, lot, 'clear')
        assert not cleared['running']
        assert all(r['status'] == 'empty' for r in spaces(client, lot))
        assert client.get(f'/api/v1/sessions?lot_id={lot}&status=active').json()['items'] == []
        closed = client.get(f'/api/v1/sessions?lot_id={lot}&status=closed').json()['items']
        assert {r['session_id'] for r in closed} == set(session_ids)
        assert client.get(f'/api/v1/sessions/{session_ids[0]}/fee').json()['payment']['payment_id'] == payment_id
        assert sum(e['action'] == 'exit' for e in cleared['events']) == 3
        assert command(client, lot, 'clear')['running'] is False
        # New arbitrary vehicle numbers can carry a type, independent of the two reserved numbers.
        result = command(client, lot, 'entry', vehicle_type='ev', vehicle_number='0077')
        row = next(r for r in spaces(client, lot) if r['space_id'] == result['space_id'])
        assert row['vehicle_type'] == row['space_type'] == 'ev'
        assert row['vehicle_number'] == '0077'


def test_automatic_runtime_enters_all_three_types_in_both_lots(tmp_path):
    with TestClient(create_runtime_app(token=TOKEN, database=tmp_path/'auto.db', mode='demo')) as client:
        for lot in ('C', 'D'):
            command(client, lot, 'start', interval_seconds=3)
        for lot in ('C', 'D'):
            rows = wait_for(lambda: spaces(client, lot), lambda rs: {r['vehicle_type'] for r in rs if r['status'] == 'occupied'} == {'general', 'ev', 'accessible'})
            assert all(r['vehicle_type'] == r['space_type'] for r in rows if r['status'] == 'occupied')
        for lot in ('C', 'D'):
            command(client, lot, 'stop')
        for lot in ('C', 'D'):
            command(client, lot, 'clear')
            assert all(r['status'] == 'empty' for r in spaces(client, lot))


def test_old_database_general_vehicle_in_new_dedicated_bay_can_exit(tmp_path):
    database = tmp_path/'legacy.db'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE simulation_targets(lot_id TEXT,space_id TEXT,occupied INTEGER,vehicle_number TEXT,plate_number TEXT,PRIMARY KEY(lot_id,space_id))')
        conn.execute("INSERT INTO simulation_targets VALUES('C','A-01',1,'8765','88가8765')")
    app = create_runtime_app(token=TOKEN, database=database, mode='demo')
    with TestClient(app) as client:
        row = wait_for(lambda: spaces(client, 'C')[0], lambda r: r['status'] == 'occupied')
        assert row['space_type'] == 'accessible' and row['vehicle_type'] == 'general'
        old_session = row['session_id']
        command(client, 'C', 'stop')
        command(client, 'C', 'clear')
        assert all(r['status'] == 'empty' for r in spaces(client, 'C'))
        closed = client.get('/api/v1/sessions?lot_id=C&status=closed').json()['items']
        assert any(r['session_id'] == old_session for r in closed)
        with closing(app.state.pipeline.connection()) as conn:
            assert conn.execute("SELECT vehicle_type FROM simulation_targets WHERE lot_id='C' AND space_id='A-01'").fetchone()[0] == 'general'


def test_real_mode_rejects_clear_and_typed_entry(tmp_path):
    with TestClient(create_runtime_app(token=TOKEN, database=tmp_path/'real.db', mode='real')) as client:
        assert client.post('/api/v1/simulation/C/clear').status_code == 403
        assert client.post('/api/v1/simulation/C/entry', json={'vehicle_type': 'ev'}).status_code == 403
