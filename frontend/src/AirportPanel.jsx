import {t} from './serviceLocale.js';
import {useId, useRef, useState} from 'react';
import {formatKST} from './uiState.js';
import './styles/airport.css';

const storageKey = 'parkon-airport-reservations';
import {airportZone, airportZones as zones} from './airportZones.js';
function readReservations() {
  try {
    const records = JSON.parse(localStorage.getItem(storageKey) || '[]');
    return Array.isArray(records) ? records : [];
  } catch {
    return [];
  }
}

export default function AirportPanel({lot}) {
  const [zone, setZone] = useState('A');
  const [reservations, setReservations] = useState(readReservations);
  const [message, setMessage] = useState('');
  const dialog = useRef(null);
  const titleId = useId();
  const available = lot.spaces.filter(space => airportZone(space) === zone && space.status === 'empty').length;

  function store(records) {
    try {
      localStorage.setItem(storageKey, JSON.stringify(records));
      setReservations(records);
      return true;
    } catch {
      setMessage(t('이 브라우저에 예약을 저장할 수 없습니다.'));
      return false;
    }
  }

  function reserve(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const start = new Date(form.get('start')).getTime();
    const end = new Date(form.get('end')).getTime();
    const duration = end - start;
    const day = 24 * 60 * 60 * 1000;
    if (!Number.isFinite(duration) || start < Date.now() || duration <= 0) {
      setMessage(t('입차는 현재 이후, 출차는 입차 이후로 지정해 주세요.'));
      return;
    }
    if ((zone === 'A' && duration > day) || (zone === 'C' && duration < day)) {
      setMessage(t('시연 예약은 P1 24시간 이하, P3 24시간 이상입니다. P2는 단기·장기 모두 선택할 수 있어요.'));
      return;
    }
    const records = readReservations();
    const capacity = lot.spaces.filter(space => airportZone(space) === zone).length;
    const overlapping = records.filter(item => item.zone === zone && item.start < end && item.end > start);
    const times = [start, ...overlapping.map(item => item.start).filter(time => time > start)];
    if (times.some(time => overlapping.filter(item => item.start <= time && item.end > time).length >= capacity)) {
      setMessage(t('해당 시간대의 예약이 가득 찼습니다.'));
      return;
    }
    const item = {id: crypto.randomUUID(), zone, start, end};
    if (store([...records, item])) setMessage(zones[zone] + t(' 예약이 저장됐습니다.'));
  }

  return <section className="panel airport-panel" aria-label={t('주차 예약')}>
    <header>
      <div><h2>{lot.name} · {t('주차 예약')}</h2><p>{t('국내선·국제선 터미널 · P3 순환 셔틀')}</p></div>
      <button type="button" className="button primary" onClick={() => {setMessage(''); dialog.current.showModal();}}>{t('예약')}</button>
    </header>
    <dialog className="airport-reservation-dialog" ref={dialog} aria-labelledby={titleId}>
      <div className="airport-dialog-heading">
        <h2 id={titleId}>{t('단기 · 장기 주차 예약')}</h2>
        <button type="button" className="button white" onClick={() => dialog.current.close()} aria-label={t('닫기')}>{t('닫기')}</button>
      </div>
      <p>{t('시연 예약은 이 브라우저에 저장됩니다. 입·출차 시간은 기기의 현지 시간 기준입니다.')}</p>
      <form className="airport-form" onSubmit={reserve}>
        <label>{t('예약 구역')}<select value={zone} onChange={event => setZone(event.target.value)}>{Object.entries(zones).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        <label>{t('입차')}<input type="datetime-local" name="start" required/></label>
        <label>{t('출차')}<input type="datetime-local" name="end" required/></label>
        <button className="button primary" type="submit">{t('예약 확정')}</button>
      </form>
      <p>{zones[zone]} {t('현재 빈자리')} {available}{t('면')}</p>
      <p role="status">{message}</p>
      <ul className="airport-events">{reservations.map(item => <li key={item.id}><div><strong>{zones[item.zone]}</strong><p>{formatKST(item.start)} ~ {formatKST(item.end)}</p></div><button type="button" className="button white" onClick={() => {if (store(readReservations().filter(row => row.id !== item.id))) setMessage(t('예약이 취소됐습니다.'));}}>{t('예약 취소')}</button></li>)}</ul>
    </dialog>
  </section>;
}
