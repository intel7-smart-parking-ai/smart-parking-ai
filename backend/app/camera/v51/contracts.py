"""Local dataset rules from the supplied 11/03 team notice, not an API adapter."""
CONTRACT_REVISION='20261104-d39-v3'
D39_NUMBERS=['23','42','57','85','14','32','55','75','10','11','12','20','21','22','24','30','33','34','40','41','43','44','58','88']

FIELD_SET='round-10-99-v1'
D39_COLORS=dict(zip(D39_NUMBERS,['green']*4+['blue']*4+['white']*16))

def displayed_prediction(row, missing='—'):
    if row.get('number'):return row['number']
    estimate=row.get('number_estimate')
    return '추정 '+estimate['number'] if estimate else missing

def sticker_colors(config):
    version=config.get('physical_sticker_set')
    if version is None:
        return D39_COLORS
    if version==FIELD_SET:
        return {}
    raise ValueError('알 수 없는 실물 스티커 배정표입니다.')

def number_config_for_lot(lot_id,config):
    if lot_id not in ('A','B','C','D'):raise ValueError('Unknown lot_id')
    allowed=(list(sticker_colors(config)) or None) if lot_id in ('A','B') else None
    return {**config,'number_mode':'physical_sticker' if lot_id in ('A','B') else 'virtual_plate',
            'allowed_numbers':allowed}
