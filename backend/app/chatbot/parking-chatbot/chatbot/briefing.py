"""설정과 검증된 차량 목록으로 만드는 현장 전달 참고 정보. 소방 판단을 대신하지 않는다."""
from . import config
from .adapters import iso, now


def build(alert, cars, known):
    lot = config.catalog.lot(alert['lot_id']) or {}
    spot = alert['spot_no']
    zones = config.catalog.zone_spots(lot['capacity'], alert['lot_id']) if lot else {}
    zone = next((config.catalog.zone_name(k) for k,v in zones.items() if spot in v), '미확인')
    ev = set(config.catalog.settings.get('ev_vehicles', []))
    def vehicle(c):
        no = c.get('vehicle_no')
        return f"{no}번 · {'전기차' if no in ev else '전기차 목록에 없음'}" if no else '번호 미인식 · 전기차 여부 미확인'
    def at(n):
        return next((vehicle(c) for c in cars if c['spot_no'] == n), '차량 없음') if known else '확인 불가'
    nearby = [f"{n}번 칸: {at(n)}" for n in (spot-1,spot+1) if 1 <= n <= lot.get('capacity',0)]
    uncertain_ev = not known or any(c.get('vehicle_no') is None for c in cars)
    ev_count = sum(c.get('vehicle_no') in ev for c in cars) if known else None
    # 장비 참고는 화재 칸과 양옆(칸 번호 차이 1)만 본다. 근거(충전 구역, 인접 전기차)가 있으면 그것을 우선하고,
    # 근거가 없을 때만 해당 범위의 미확인(목록 실패, 번호 미인식)을 "대비 필요"로 알린다.
    near_cars = [c for c in cars if abs(c['spot_no']-spot) <= 1] if known else []
    relevant = zone == config.catalog.zone_name('ev') or any(c.get('vehicle_no') in ev for c in near_cars)
    near_unknown = not known or any(c.get('vehicle_no') is None for c in near_cars)
    equipment = ('질식소화덮개·이동식 수조 필요 가능성 (판단은 소방)' if relevant else
                 '전기차 여부 미확인 — 대비 필요 (장비 필요성 판단은 소방)' if near_unknown else
                 '전기차·충전 구역 해당 여부를 현장에서 확인 (판단은 소방)')
    facility = lot.get('facility', {})
    def flag(k): return '미확인' if facility.get(k) is None else '있음' if facility[k] else '없음'
    height = facility.get('height_limit_m')
    data = {'location':f"{config.catalog.name(alert['lot_id'])} {spot}번 칸 · {zone} 구역",
            'vehicle':at(spot), 'adjacent':nearby, 'ev_count':ev_count,
            'ev_count_text': '확인 불가' if not known else f"확인된 전기차 {ev_count}대" + (' · 번호 미인식 차량 포함, 전체 수 미확인' if uncertain_ev else ''),
            'equipment':equipment, 'exits':lot.get('exits') or [lot.get('exit','미확인')],
            'facility':f"지하: {flag('underground')} / 높이 제한: {str(height)+'m' if height is not None else '미확인'} / 스프링클러: {flag('sprinkler')}",
            'notice':'시연용 모의 상황이며 실제 화재가 아님', 'updated_at':iso(now())}
    data['text'] = '\n'.join(['[소방 전달용 현장 정보 · 설정값은 PROVISIONAL]',data['notice'],
        '위치: '+data['location'],'해당 칸: '+data['vehicle'],'양옆: '+' / '.join(nearby),
        '주차장 내 전기차: '+data['ev_count_text'],'장비 참고: '+equipment,
        '출구·진입 참고: '+' / '.join(data['exits']),'설비: '+data['facility'],'정보 기준: '+data['updated_at']])
    return data
