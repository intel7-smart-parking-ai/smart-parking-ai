import {t} from './serviceLocale.js';
import {useState} from 'react';
import {formatKST} from './uiState.js';
import './styles/airport.css';

const storageKey = 'parkon-airport-reservations';
import {airportZone, airportZones as zones} from './airportZones.js';
const scenarios = [
  {id: 'blind', label: '사각지대 주차 차량', detail: 'P3 화물청사 뒤편 서비스 통로에 차량 정차 신호가 감지됐습니다. 현장 순찰을 요청하세요.'},
  {id: 'theft', label: '도난 의심', detail: 'P3 여객·화물 주차 구역에서 차량 주변 이상 접근 신호가 감지됐습니다. 관제 영상을 확인하세요.'},
  {id: 'damage', label: '파손 의심', detail: 'P1 승하차 동선 부근에서 충격 신호가 감지됐습니다. 현장 확인을 요청하세요.'},
];

function readReservations() {
  try {
    const records = JSON.parse(localStorage.getItem(storageKey) || '[]');
    return Array.isArray(records) ? records : [];
  } catch {
    return [];
  }
}

export default function AirportPanel({lot, admin = false}) {
  const [zone, setZone] = useState('A');
  const [rush, setRush] = useState(false);
  const [events, setEvents] = useState([]);
  const [reservations, setReservations] = useState(readReservations);
  const [message, setMessage] = useState('');
  const forecast = Object.entries(zones).map(([id, label]) => {
    const spaces = lot.spaces.filter(space => airportZone(space) === id);
    const available = spaces.filter(space => space.status === 'empty').length;
    const expected = Math.max(0, available - (rush ? (id === 'A' ? 4 : 2) : 1));
    return {id, label, available, expected};
  });
  const best = forecast.reduce((a, b) => a.expected >= b.expected ? a : b);
  const anotherZone = forecast.find(item => item.id === zone);

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

  function simulate(scenario) {
    setEvents(current => [{...scenario, eventId: crypto.randomUUID(), at: Date.now(), checked: false}, ...current]);
  }

  return <section className="panel airport-panel" aria-label={t("김해공항 주차 시뮬레이션")}>
    <header><div><h2>{t("김해공항 주차 시뮬레이션")}</h2><p>{t("국내선·국제선 터미널 · P3 순환 셔틀")}</p></div><span className="badge virtual">{t("가상 공항")}</span></header>
    <div className="airport-section">
      <h3>{t("30분 후 혼잡 예측 · 시연")}</h3>
      <label><input type="checkbox" checked={rush} onChange={event => setRush(event.target.checked)}/> {t("항공편 집중 시간 가정")}</label>
      <div className="airport-forecast">{forecast.map(item => <div key={item.id}><strong>{item.label}</strong><span>{t("현재 빈자리")} {item.available}{t("면")}</span><span>{t("30분 후")} {item.expected}{t("면 ·")} {item.expected === 0 ? t('만차 예상') : item.expected <= 2 ? t('혼잡 예상') : t('여유 예상')}</span></div>)}</div>
      <p role="status">{best.expected ? best.label + t(' 진입을 추천합니다.') : t('모든 구역이 만차 예상입니다. 진입 전 대기 시간을 확보해 주세요.')} {t("현재 빈자리와 시연 유입량을 사용한 추정입니다.")}</p>
    </div>
    {admin ? <div className="airport-section">
      <h3>공항 안전 관제 · 이벤트</h3>
      <div className="airport-actions">{scenarios.map(scenario => <button key={scenario.id} type="button" className="button white" onClick={() => simulate(scenario)}>{scenario.label} 시연</button>)}</div>
      <p role="status">{t("미확인")} {events.filter(item => !item.checked).length}건</p>
      <ul className="airport-events">{events.map(item => <li key={item.eventId}><div><strong>{item.label}</strong><p>{item.detail}</p><small>{formatKST(item.at)}</small></div><button type="button" className="button white" disabled={item.checked} onClick={() => setEvents(current => current.map(row => row.eventId === item.eventId ? {...row, checked: true} : row))}>{item.checked ? t('확인 완료') : t('확인')}</button></li>)}</ul>
    </div> : <div className="airport-section">
      <h3>{t("단기 · 장기 주차 예약")}</h3>
      <p>{t("시연 예약은 이 브라우저에 저장됩니다. 입·출차 시간은 기기의 현지 시간 기준입니다.")}</p>
      <form className="airport-form" onSubmit={reserve}>
        <label>{t("예약 구역")}<select value={zone} onChange={event => setZone(event.target.value)}>{Object.entries(zones).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        <label>{t("입차")}<input type="datetime-local" name="start" required/></label>
        <label>{t("출차")}<input type="datetime-local" name="end" required/></label>
        <button className="button primary" type="submit">{t("예약")}</button>
      </form>
      <p>{anotherZone.label} {t("현재 빈자리")} {anotherZone.available}{t("면")}</p>
      <p role="status">{message}</p>
      <ul className="airport-events">{reservations.map(item => <li key={item.id}><div><strong>{zones[item.zone]}</strong><p>{formatKST(item.start)} ~ {formatKST(item.end)}</p></div><button type="button" className="button white" onClick={() => {if (store(readReservations().filter(row => row.id !== item.id))) setMessage(t('예약이 취소됐습니다.'));}}>{t("예약 취소")}</button></li>)}</ul>
    </div>}
  </section>;
}
