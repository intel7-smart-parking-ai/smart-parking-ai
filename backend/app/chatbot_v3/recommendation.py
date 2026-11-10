"""Deterministic general-space ranking with fresh occupancy and complete alert reads."""
from datetime import datetime, timezone
from .schemas import ToolResult
from .tools import failure


def recommend_spaces(tools, knowledge, lot_id, destination_id, *, preference='nearest', limit=3, now=None, max_age_seconds=10):
    if preference not in ('nearest', 'easy') or type(limit) is not int or not 1 <= limit <= 5 or max_age_seconds <= 0:
        return failure('invalid_input', 'INVALID_PREFERENCE', '추천 조건을 확인해주세요.')
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        return failure('invalid_input', 'INVALID_CLOCK', '기준 시각에 시간대가 필요합니다.')

    def fresh(value):
        try:
            clock = now if now is not None else datetime.now(timezone.utc)
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return stamp.tzinfo is not None and 0 <= (clock - stamp).total_seconds() <= max_age_seconds
        except (TypeError, AttributeError, ValueError):
            return False

    candidates = knowledge.get_destination_candidates(lot_id, destination_id)
    if candidates.status != 'ok':
        return candidates
    alerts = tools.list_alerts(lot_id)
    if alerts.status != 'ok':
        return alerts
    if not alerts.data['complete'] or not fresh(alerts.data['as_of']):
        return failure('unavailable', 'ALERTS_UNCONFIRMED', '최신 경보 전체를 확인하지 못해 주차 추천을 보류합니다.')
    if any(r['level'] >= 2 for r in alerts.data['active_items']):
        return failure('unavailable', 'EVACUATION_ACTIVE', '진행 중인 모의 경보가 있어 주차 추천을 중단합니다. 현장 안내를 확인해주세요.')
    snapshot = tools.list_spaces(lot_id)
    if snapshot.status != 'ok':
        return snapshot
    if not fresh(alerts.data['as_of']):
        return failure('unavailable', 'ALERTS_UNCONFIRMED', '주차칸 조회 중 경보 정보가 오래되어 추천을 보류합니다.')
    if snapshot.data['next_cursor'] is not None or not fresh(snapshot.data['as_of']):
        return failure('unavailable', 'SPACES_UNCONFIRMED', '최신 주차칸 전체를 확인하지 못했습니다.')
    live = {r['space_id']: r for r in snapshot.data['items']}
    items, excluded = [], []
    for rank, sid in enumerate(candidates.data['ranked_general_space_ids'], 1):
        row = live.get(sid)
        reason = 'missing' if row is None else 'reserved_type' if row['space_type'] != 'general' else 'not_empty' if row['status'] != 'empty' else 'stale' if not fresh(row['observed_at']) else None
        if reason:
            excluded.append({'space_id': sid, 'reason': reason})
            continue
        context = knowledge.get_space_context(lot_id, sid)
        if context.status != 'ok':
            return context
        items.append({'space_id': sid, 'access_rank': rank, 'reason': candidates.data['reason'], 'features': context.data['features'], 'nearby_facilities': context.data['nearby_facilities'], 'observed_at': row['observed_at']})
    if preference == 'easy':
        items.sort(key=lambda r: ('주차 편의 우선 추천' not in r['features'], r['access_rank']))
    items = items[:limit]
    data = {'lot_id': lot_id, 'destination_id': destination_id, 'items': items, 'excluded': excluded, 'as_of': snapshot.data['as_of'], 'knowledge_version': knowledge.version, 'scope': knowledge.scope, 'notice': '시설과 인접 관계는 프로젝트 가상 설정입니다. 빈자리 조회는 예약이 아니며 현장에서 변경될 수 있습니다.', 'active_alerts': alerts.data['active_items'], 'knowledge_is_scenario': True, 'live_snapshot_is_mock': snapshot.is_mock, 'space_sources': [{'space_id': r['space_id'], 'source': live[r['space_id']].get('source'), 'is_mock': live[r['space_id']].get('is_mock')} for r in items]}
    return ToolResult(status='ok', data=data, source_refs=candidates.source_refs + alerts.source_refs + snapshot.source_refs, is_mock=snapshot.is_mock)
