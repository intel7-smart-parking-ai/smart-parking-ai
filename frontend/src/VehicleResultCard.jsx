import EntryVehiclePhoto from './EntryVehiclePhoto.jsx';
import {Icon,money} from './components.jsx';
import {t} from './serviceLocale.js';
import './styles/vehicle-result-card.css';

export default function VehicleResultCard({result,lotName,onMap}) {
 const plate=result.lookup_type==='sticker'?t('스티커 ')+result.car_id:result.plate_number||result.vehicle_number;
 return <section className="vehicle-result-card" aria-label={t('차량의 현재 주차 위치')}>
  <div className="vehicle-result-info">
   <span className="vehicle-result-number">{plate}</span>
   <div className="vehicle-result-location"><Icon name="pin"/><strong>{lotName} · {result.space_id}</strong></div>
   {result.fee_notice&&<p>{result.fee_notice}</p>}
   <div className="vehicle-result-fee"><span>{result.fee?.payment?t('결제 완료 금액'):t('예상 주차요금')}</span><b>{result.fee?money(result.fee.amount_krw):t('요금 정보 없음')}</b></div>
   <small>{t('서버 이용 시간 기준 요금')}</small>
  </div>
  {['C','D'].includes(result.lot_id)&&<EntryVehiclePhoto plate={plate}/>}
  <button type="button" className="vehicle-result-map" aria-label={t('지도에서 보기')} onClick={onMap}><Icon name="arrow"/></button>
 </section>;
}
