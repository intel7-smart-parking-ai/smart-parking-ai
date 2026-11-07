"""Fixed, validated server reads; fee GET is never automatically retried."""
from copy import deepcopy
import re
from threading import RLock
from urllib.parse import quote
import httpx
from pydantic import ValidationError
from .schemas import AlertList, Fee, Location, LotList, PolicyList, SpaceList, ToolError, ToolResult


def failure(status, code, message, http_status=None):
    return ToolResult(status=status, error=ToolError(code=code, message=message, http_status=http_status))


class ParkingTools:
    def __init__(self, client: httpx.Client, timeout=3, max_fee_requests=1000):
        self.client = client
        self.timeout = timeout
        self.max_fee_requests = max_fee_requests
        self._fees = {}
        self._fee_lock = RLock()

    def _get(self, path, schema, *, params=None, vehicle=False):
        try:
            response = self.client.get('/api/v1' + path, params=params, timeout=self.timeout, follow_redirects=False)
        except httpx.RequestError:
            return failure('unavailable', 'BACKEND_UNAVAILABLE', '서버 응답을 확인하지 못했습니다. 잠시 후 새 요청으로 확인해주세요.')
        if response.status_code != 200:
            code = response.status_code
            if code == 404 and vehicle:
                try:
                    unsupported = response.json().get('detail') == 'Not Found'
                except (ValueError, AttributeError):
                    unsupported = False
                if unsupported:
                    return failure('unsupported', 'LOOKUP_UNSUPPORTED', '이 서버에서 해당 차량 조회를 제공하지 않습니다.', code)
                return failure('ambiguous', 'LOCATION_UNRESOLVED', '단일 차량 위치를 확인하지 못했습니다. 차량이 없거나 여러 위치가 일치할 수 있습니다.', code)
            status = 'not_found' if code == 404 else 'invalid_input' if code == 422 else 'unavailable'
            return failure(status, 'BACKEND_ERROR', '서버에서 요청을 처리하지 못했습니다.', code)
        try:
            data = schema.model_validate_json(response.content).model_dump(mode='json')
        except (ValidationError, ValueError):
            return failure('invalid_data', 'INVALID_RESPONSE', '서버 응답 형식을 확인하지 못했습니다.')
        is_mock = data.get('is_mock')
        if response.headers.get('X-ParkFlow-Data-Source') == 'mock':
            is_mock = True
        return ToolResult(status='ok', data=data, source_refs=['api:/api/v1' + path], is_mock=is_mock)

    def list_lots(self):
        return self._get('/parking-lots', LotList)

    def list_spaces(self, lot_id):
        if lot_id not in ('A', 'B', 'C', 'D'):
            return failure('invalid_input', 'INVALID_LOT', '주차장을 확인해주세요.')
        result = self._get(f'/parking-lots/{lot_id}/spaces', SpaceList)
        if result.status == 'ok':
            result.data['lot_id'] = lot_id
        return result

    def find_vehicle(self, lookup_type, value):
        patterns = {'sticker': r'[1-9][0-9]', 'plate_suffix': r'[0-9]{4}'}
        if lookup_type not in patterns or not isinstance(value, str) or not re.fullmatch(patterns[lookup_type], value):
            return failure('invalid_input', 'INVALID_VEHICLE', '실물 스티커 두 자리 또는 가상 차량 뒤 네 자리를 확인해주세요.')
        if lookup_type == 'sticker':
            result = self._get(f'/vehicles/{value}/location', Location, vehicle=True)
        else:
            result = self._get('/vehicle-search', Location, params={'vehicle_number': value}, vehicle=True)
        if result.status == 'ok':
            key = 'car_id' if lookup_type == 'sticker' else 'vehicle_number'
            if result.data.get(key) != value or (lookup_type == 'plate_suffix' and result.data['lot_id'] not in ('C', 'D')):
                return failure('invalid_data', 'LOCATION_MISMATCH', '조회한 차량과 서버 응답이 일치하지 않습니다.')
            result.data['lookup_type'] = lookup_type
        return result

    def get_fee(self, session_id, request_id):
        if not isinstance(session_id, str) or not session_id.strip() or not isinstance(request_id, str) or not request_id.strip():
            return failure('invalid_input', 'INVALID_FEE_REQUEST', '세션과 요청 식별자가 필요합니다.')
        with self._fee_lock:
            previous = self._fees.get(request_id)
            if previous:
                if previous[0] != session_id:
                    return failure('conflict', 'REQUEST_ID_CONFLICT', '동일 요청 식별자를 다른 세션에 사용할 수 없습니다.')
                return deepcopy(previous[1])
            if len(self._fees) >= self.max_fee_requests:
                return failure('unavailable', 'FEE_CACHE_FULL', '요금 조회 요청 보관 한도에 도달했습니다.')
            result = self._get('/sessions/' + quote(session_id, safe='') + '/fee', Fee)
            if result.status == 'ok' and result.data['session_id'] != session_id:
                result = failure('invalid_data', 'SESSION_MISMATCH', '요금 세션이 일치하지 않습니다.')
            # Memoize uncertain outcomes too: the server may already have stored a quote.
            self._fees[request_id] = (session_id, deepcopy(result))
            return result

    def list_rate_policies(self):
        return self._get('/rate-policies', PolicyList)

    def list_alerts(self, lot_id):
        if lot_id not in ('A', 'B', 'C', 'D'):
            return failure('invalid_input', 'INVALID_LOT', '주차장을 확인해주세요.')
        result = self._get('/alerts', AlertList, params={'lot_id': lot_id, 'limit': 500})
        if result.status == 'ok':
            data = result.data
            if any(row['lot_id'] != lot_id for row in data['items']):
                return failure('invalid_data', 'ALERT_LOT_MISMATCH', '경보 주차장이 일치하지 않습니다.')
            data['complete'] = data['next_cursor'] is None and data['total'] == len(data['items']) and len(data['items']) < 500
            data['active_items'] = [r for r in data['items'] if r['status'] != 'resolved']
            data['no_active_alerts_confirmed'] = data['complete'] and not data['active_items']
        return result
