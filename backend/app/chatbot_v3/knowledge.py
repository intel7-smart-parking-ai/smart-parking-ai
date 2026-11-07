"""Local fictional facility knowledge, explicitly separated from live server facts."""
from copy import deepcopy
import json
from pathlib import Path
from .schemas import ToolResult
from .tools import failure

DEFAULT_PATH = Path(__file__).resolve().parents[3] / 'docs/parking-knowledge/parking_knowledge.v0.1.json'


class KnowledgeStore:
    def __init__(self, path=DEFAULT_PATH):
        document = json.loads(Path(path).read_text(encoding='utf-8'))
        self.version = document['version']
        self.scope = document['scope']
        self.lots = {}
        for lot in document['lots']:
            lid = lot['lot_id']
            if lid not in ('A', 'B', 'C', 'D') or lid in self.lots:
                raise ValueError('invalid/duplicate knowledge lot')
            spaces = {s['space_id']: s for s in lot['spaces']}
            facilities = {f['facility_id']: f for f in lot['facilities']}
            if len(spaces) != len(lot['spaces']) or len(spaces) != lot['capacity'] or len(facilities) != len(lot['facilities']):
                raise ValueError('invalid knowledge counts')
            for facility in facilities.values():
                if any(s not in spaces for s in facility['nearby_space_ids']):
                    raise ValueError('unknown nearby space')
            destinations = set()
            for ranking in lot['recommendations']:
                ids = ranking['ranked_general_space_ids']
                dest = ranking['destination_id']
                if dest in destinations or dest not in facilities or len(ids) != len(set(ids)):
                    raise ValueError('invalid destination ranking')
                destinations.add(dest)
                if any(s not in spaces or spaces[s]['space_type'] != 'general' for s in ids):
                    raise ValueError('rankings must refer to general spaces')
            self.lots[lid] = deepcopy(lot)

    def _result(self, ref, data):
        return ToolResult(status='ok', data=deepcopy({'knowledge_version': self.version, 'scope': self.scope, **data}), source_refs=[f'knowledge:{self.version}:{ref}'], is_mock=True)

    def get_place_catalog(self):
        return self._result('places', {'items': [{key: lot[key] for key in ('lot_id', 'name', 'language_profile')} for lot in self.lots.values()]})

    def get_facilities(self, lot_id, facility_type=None):
        if lot_id not in self.lots:
            return failure('invalid_input', 'INVALID_LOT', '주차장을 확인해주세요.')
        items = [f for f in self.lots[lot_id]['facilities'] if facility_type is None or f['type'] == facility_type]
        return self._result(lot_id + ':facilities', {'lot_id': lot_id, 'items': items})

    def get_space_context(self, lot_id, space_id):
        lot = self.lots.get(lot_id)
        if not lot:
            return failure('invalid_input', 'INVALID_LOT', '주차장을 확인해주세요.')
        space = next((s for s in lot['spaces'] if s['space_id'] == space_id), None)
        if space is None:
            return failure('not_found', 'UNKNOWN_SPACE', '설정집에 해당 칸이 없습니다.')
        return self._result(f'{lot_id}:{space_id}', {'lot_id': lot_id, **space, 'nearby_facilities': [f for f in lot['facilities'] if space_id in f['nearby_space_ids']]})

    def get_destination_candidates(self, lot_id, destination_id):
        lot = self.lots.get(lot_id)
        if not lot:
            return failure('invalid_input', 'INVALID_LOT', '주차장을 확인해주세요.')
        ranking = next((r for r in lot['recommendations'] if r['destination_id'] == destination_id), None)
        if ranking is None:
            return failure('unsupported', 'UNKNOWN_DESTINATION', '해당 목적지의 추천 설정이 없습니다.')
        return self._result(f'{lot_id}:{destination_id}', {'lot_id': lot_id, **ranking})

    def get_lot_guidance(self, lot_id):
        lot = self.lots.get(lot_id)
        if not lot:
            return failure('invalid_input', 'INVALID_LOT', '주차장을 확인해주세요.')
        return self._result(lot_id + ':guidance', {k: v for k, v in lot.items() if k not in ('spaces', 'facilities', 'recommendations', 'language_profile')})


ParkingKnowledge = KnowledgeStore
