"""V3 격리 시험. Session36은 시험 모델이며 공통 세션 구현이 아니다."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import CheckConstraint, String, Uuid, create_engine, select
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import NullPool

from backend.app.api import billing_router as billing
from backend.app.billing import db, payment_service

NOW = '2026-11-03T09:55:00+09:00'
ENTRY = '2026-11-03T09:00:00+09:00'
IDS = ['ses_legacy_01', 'ses_' + 'a' * 32, str(uuid4())]


class ProbeBase(DeclarativeBase):
    pass


class Session36(ProbeBase):
    __tablename__ = 'parking_sessions'
    __table_args__ = (CheckConstraint(
        "(status='active' AND exit_at IS NULL) OR "
        "(status='closed' AND exit_at IS NOT NULL AND exit_at>=entry_at)", name='v3_lifecycle'),)
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    lot_id: Mapped[str] = mapped_column(String)
    space_id: Mapped[str | None] = mapped_column(String)
    car_id: Mapped[str | None] = mapped_column(String)
    entry_at: Mapped[str] = mapped_column(String)
    exit_at: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    path = tmp_path / ('kan17-v3-' + uuid4().hex + '.sqlite3')
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    identity = path.stat()
    original = sqlite3.connect
    connections = []

    def connect(database=path, *args, **kwargs):
        if Path(database).absolute() != path:
            raise AssertionError('V3에서 생성한 임시 DB 외 접근 금지')
        current = path.lstat()
        assert not path.is_symlink() and current.st_nlink == 1
        assert (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino)
        kwargs.setdefault('check_same_thread', False)
        conn = original(path, *args, **kwargs)
        connections.append(conn)
        conn.execute('PRAGMA foreign_keys=ON')
        return conn

    monkeypatch.setattr(sqlite3, 'connect', connect)
    monkeypatch.setenv('SMART_PARKING_DB_PATH', str(path))
    monkeypatch.setattr(payment_service, 'now_kst_iso', lambda: NOW)
    monkeypatch.setattr(billing, 'now_kst_iso', lambda: NOW)
    engine = create_engine('sqlite://', creator=connect, poolclass=NullPool)
    app = FastAPI()
    app.include_router(billing.billing_router)

    @contextmanager
    def setup(kind):
        if kind == 'string36':
            ProbeBase.metadata.create_all(engine)  # 직접 새로 만든 임시 시험 DB에만 적용한다.
        with connect() as conn:
            db.init_schema(conn)
            conn.execute("INSERT INTO parking_lots VALUES ('A','V3 합성','real',1)")
            conn.execute("INSERT INTO rate_policies VALUES ('A',30,1000,10,500,10000)")
        try:
            with TestClient(app) as client:
                yield client, engine, connect
        finally:
            engine.dispose()

    try:
        yield setup
    finally:
        engine.dispose()
        for conn in connections:
            conn.close()


@pytest.mark.parametrize('kind', ['billing_text', 'string36'])
@pytest.mark.parametrize('sid', IDS, ids=['legacy', 'legacy36', 'uuid36'])
@pytest.mark.parametrize('state', ['active', 'closed'])
def test_id_roundtrip_fee_payment_replay_cancel(isolated, kind, sid, state):
    with isolated(kind) as (client, engine, connect):
        exit_at = NOW if state == 'closed' else None
        if kind == 'string36':
            with Session(engine) as session:
                session.add(Session36(session_id=sid, lot_id='A', space_id='A-01', car_id='10',
                                      entry_at=ENTRY, exit_at=exit_at, status=state))
                session.commit()
                found = session.get(Session36, sid)
                assert found.session_id == sid and found.status == state
        else:
            with connect() as conn:
                conn.execute('INSERT INTO parking_sessions '
                             '(session_id, lot_id, space_id, car_id, entry_at, exit_at) VALUES (?,?,?,?,?,?)',
                             (sid, 'A', 'A-01', '10', ENTRY, exit_at))
        fee = client.get(f'/api/v1/sessions/{sid}/fee', params={'now': NOW})
        assert fee.status_code == 200
        assert fee.json()['session_id'] == sid and fee.json()['amount_krw'] == 2500
        quote_id = fee.json()['quote_id']
        with connect() as conn:
            assert conn.execute('SELECT session_id FROM fee_quotes WHERE quote_id=?', (quote_id,)).fetchone()[0] == sid
        payload = {'session_id': sid, 'quote_id': quote_id, 'idempotency_key': 'v3-key'}
        paid = client.post('/api/v1/mock-payments', json=payload)
        assert paid.status_code == 200 and paid.json()['session_id'] == sid
        assert paid.json()['amount_krw'] == 2500
        replay = client.post('/api/v1/mock-payments', json=payload)
        assert replay.status_code == 200 and replay.json()['idempotent_replayed'] is True
        assert replay.json()['payment_id'] == paid.json()['payment_id']
        conflict = client.post('/api/v1/mock-payments', json=payload | {'idempotency_key': 'other'})
        assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'ALREADY_PAID'
        listed = client.get('/api/v1/mock-payments').json()['items']
        assert len(listed) == 1 and listed[0]['session_id'] == sid
        canceled = client.post('/api/v1/mock-payments/' + paid.json()['payment_id'] + '/cancel')
        assert canceled.status_code == 200 and canceled.json()['session_id'] == sid
        with connect() as conn:
            row = conn.execute('SELECT session_id,exit_at FROM parking_sessions').fetchone()
            assert tuple(row) == (sid, exit_at)  # 결제/취소가 주차 상태를 바꾸지 않는다.


def test_mixed_ids_and_cross_session_key_conflict(isolated):
    with isolated('string36') as (client, engine, connect):
        with Session(engine) as session:
            session.add_all([Session36(session_id=sid, lot_id='A', space_id='A-01', car_id='10',
                                       entry_at=ENTRY, exit_at=NOW, status='closed') for sid in IDS])
            session.commit()
            assert set(session.scalars(select(Session36.session_id))) == set(IDS)
        quotes = {}
        for index, sid in enumerate(IDS):
            fee = client.get(f'/api/v1/sessions/{sid}/fee', params={'now': NOW})
            assert fee.status_code == 200 and fee.json()['session_id'] == sid
            quotes[sid] = fee.json()['quote_id']
            assert client.post('/api/v1/mock-payments', json={
                'session_id': sid, 'quote_id': quotes[sid], 'idempotency_key': f'key-{index}'}).status_code == 200
        response = client.post('/api/v1/mock-payments', json={
            'session_id': IDS[1], 'quote_id': quotes[IDS[1]], 'idempotency_key': 'key-0'})
        assert response.status_code == 409 and response.json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
        assert {r['session_id'] for r in client.get('/api/v1/mock-payments').json()['items']} == set(IDS)
        missing = client.get('/api/v1/sessions/ses_missing/fee')
        assert missing.status_code == 404 and missing.json()['error']['code'] == 'SESSION_NOT_FOUND'


@pytest.mark.parametrize('state,exit_at', [('parked', None), ('active', NOW), ('closed', None)])
def test_probe_lifecycle_rejects_invalid_combinations(isolated, state, exit_at):
    from sqlalchemy.exc import IntegrityError
    with isolated('string36') as (_, engine, connect):
        with Session(engine) as session:
            session.add(Session36(session_id=IDS[0], lot_id='A', space_id='A-01', car_id='10',
                                  entry_at=ENTRY, exit_at=exit_at, status=state))
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()


def test_sqlite_string36_does_not_enforce_length(isolated):
    assert Session36.__table__.c.session_id.type.length == 36
    long_id = 'ses_' + 'x' * 40
    with isolated('string36') as (_, engine, connect):
        with Session(engine) as session:
            session.add(Session36(session_id=long_id, lot_id='A', space_id=None, car_id=None,
                                  entry_at=ENTRY, exit_at=None, status='active'))
            session.commit()
            assert session.get(Session36, long_id).session_id == long_id


def test_original_uuid_type_cannot_bind_legacy_string():
    # 개인 원본의 Mapped[UUID]와 같은 타입 처리. 원본 전체 모델/DB는 로드하지 않는다.
    processor = Uuid(as_uuid=True).bind_processor(sqlite.dialect())
    with pytest.raises(AttributeError):
        processor(IDS[0])
    assert processor(UUID(IDS[2])) == UUID(IDS[2]).hex


def test_session_list_not_implemented_and_location_is_mock():
    from backend.app.main import app
    with TestClient(app) as client:
        assert client.get('/api/v1/sessions').status_code == 404
        assert client.get('/api/v1/vehicles/10/location').json()['session_id'] is None
