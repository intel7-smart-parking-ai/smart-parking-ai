import ChatPanel from './ChatPanel.jsx';
import {useCallback, useEffect, useRef, useState} from 'react';
import {loadLots, lookupVehicle, dataSource, pollInterval} from './api.js';
import {Detail, Icon, Search, money} from './components.jsx';
import ParkingLayoutMap from './ParkingLayoutMap.jsx';
import InstallApp from './InstallApp.jsx';
import './styles/mobile-app.css';

export default function MobileParkingApp() {
  const [tab, setTab] = useState('home');
  const [lots, setLots] = useState([]);
  const [lotId, setLotId] = useState('A');
  const [filter, setFilter] = useState('all');
  const [kind, setKind] = useState('sticker');
  const [car, setCar] = useState('');
  const [result, setResult] = useState(null);
  const [space, setSpace] = useState(null);
  const [loading, setLoading] = useState(true);
  const [searching, setSearching] = useState(false);
  const [connected, setConnected] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(null);
  const [error, setError] = useState('');
  const refreshing = useRef(false);
  const searchLock = useRef(false);
  const refresh = useCallback(async () => {
    if (refreshing.current) return;
    refreshing.current = true;
    try {
      const data = await loadLots();
      setLots(data); setUpdatedAt(dataSource==='team'&&data[0]?.updated_at?new Date(data[0].updated_at):new Date()); setConnected(true);
    } catch {
      setConnected(false);
    } finally {setLoading(false); refreshing.current = false;}
  }, []);
  useEffect(() => {
    document.title = 'PARK:ON · 주차장 앱';
    refresh();
    const timer = setInterval(() => {if (!document.hidden) refresh();}, pollInterval);
    const offline = () => setConnected(false);
    const online = () => refresh();
    const visible = () => {if (!document.hidden) refresh();};
    window.addEventListener('offline', offline);
    window.addEventListener('online', online);
    document.addEventListener('visibilitychange', visible);
    return () => {clearInterval(timer); window.removeEventListener('offline', offline); window.removeEventListener('online', online); document.removeEventListener('visibilitychange', visible);};
  }, [refresh]);
  const lot = lots.find(item => item.lot_id === lotId);
  const empty = item => item.spaces.filter(bay => bay.status === 'empty').length;
  const totalEmpty = lots.reduce((count, item) => count + empty(item), 0);
  function openLot(item) {
    setLotId(item.lot_id); setKind(dataSource==='team'||item.type === 'real' ? 'sticker' : 'plate');
    setCar(''); setResult(null); setSpace(null); setFilter('all'); setTab('map'); setError('');
    window.scrollTo({top: 0});
  }
  function switchKind(value) {setKind(value); setCar(''); setResult(null); setError('');}
  async function find(number, searchKind = kind) {
    if (searchLock.current || !connected) return;
    searchLock.current = true; setSearching(true); setResult(null); setError('');
    try {
      const data = await lookupVehicle(number, searchKind);
      setCar(number); setResult(data);
    } catch (failure) {setError(failure.message);}
    finally {setSearching(false); searchLock.current = false;}
  }
  const nav = [['home','주차장','grid'],['map','주차 지도','pin'],['search','내 차·요금','car'],['help','이용 안내','info'],['chat','주차 안내','info']];
  return <div className="mobile-parking-app">
    <header className="mobile-app-header"><a href="/?app=1" aria-label="PARK:ON 앱 홈"><img src="/icons/icon-192.png" alt=""/><b>PARK<span>:ON</span></b></a><div className="mobile-app-header-actions"><a className="app-switch-web" href="/?app=0">웹으로 보기 <Icon name="arrow"/></a><InstallApp/></div></header>
    <main className="mobile-app-main">
      <div className={`app-connection ${connected ? 'online' : 'offline'}`} role="status"><span><i/>{loading ? '주차 정보를 불러오는 중' : connected ? (dataSource==='demo'?'서버 연결됨 · 데모 데이터':'팀 API 연결됨') : '연결 끊김 · 빈자리 재확인 필요'}</span><button onClick={refresh} aria-label="주차 정보 새로고침"><Icon name="refresh"/></button></div>
      {!loading && !connected && <div className="app-offline-notice" role="alert">{lots.length ? '현재 보이는 정보는 이전 조회값입니다. 연결이 복구되면 다시 확인해주세요.' : '서버에 연결할 수 없습니다. 네트워크와 서버 상태를 확인해주세요.'}<button onClick={refresh}>다시 연결</button></div>}
      {tab === 'home' && <>
        <section className="app-home-hero"><p>FIND YOUR PARKING</p><div><strong>{loading || !lots.length ? '—' : totalEmpty}<small>자리</small></strong><span>{connected ? '현재 조회된 주차 가능 공간' : '이전 조회의 빈자리 수'}<br/><b>실물 {lots.filter(l=>l.type==='real').length}곳 · 가상 {lots.filter(l=>l.type==='virtual').length}곳</b></span></div></section>
        <div className="app-section-heading"><h2>어디에 주차할까요?</h2><span>{updatedAt ? updatedAt.toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit',second:'2-digit'}) + ' 조회' : '조회 대기'}</span></div>
        <div className="app-lot-list">{lots.map((item, index) => <button key={item.lot_id} className="app-lot-card" onClick={() => openLot(item)}>
          <span className={`app-lot-number number-${index}`}>0{index+1}</span><span className="app-lot-name"><b>{item.name}</b><small>{dataSource==='team'||item.type === 'real' ? '스티커로 찾기' : '가상 · 차량번호로 찾기'}</small><span className="app-lot-progress"><i style={{width: `${empty(item)/item.spaces.length*100}%`}}/></span><small>전체 {item.spaces.length}면</small></span><span className="app-free-count"><strong>{empty(item)}</strong><small>자리 가능</small><Icon name="arrow"/></span>
        </button>)}</div>
        {loading && <div className="app-placeholder">주차장 정보를 가져오고 있습니다…</div>}
        <p className="app-data-note">{dataSource==='demo'?'현재는 목업 데이터입니다.':'팀 API 조회 결과입니다.'} 실제 카메라의 주차 현황은 연동 전이며, 빈자리 정보는 공간 예약을 의미하지 않습니다.</p>
      </>}
      {tab === 'map' && <><div className="app-section-heading"><div><p className="app-eyebrow">PARKING MAP</p><h1>빈자리는 어디에?</h1></div></div><div className="app-lot-switch" aria-label="지도 주차장 선택">{lots.map((item, index) => <button key={item.lot_id} className={lotId === item.lot_id ? 'selected' : ''} onClick={() => openLot(item)} aria-pressed={lotId === item.lot_id}>{index+1}번<small>{empty(item)}자리</small></button>)}</div>{lot && <><div className="app-map-summary"><b>{lot.name}</b><span>빈자리 <strong>{empty(lot)}</strong> / {lot.spaces.length}면</span></div><ParkingLayoutMap key={lot.lot_id} lot={lot} filter={filter} setFilter={setFilter} alerts={[]} found={result} onSpace={setSpace}/><button className="app-primary" onClick={() => {setTab('search'); setKind(dataSource==='team'||lot.type === 'real' ? 'sticker' : 'plate'); setResult(null);}}>이 주차장에서 내 차 찾기</button></>}</>}
      {tab === 'search' && <><p className="app-eyebrow">FIND MY CAR</p><h1>내 차와 이용요금</h1><p className="app-intro">실물은 스티커로, 가상은 차량번호로 찾으세요.</p><section className="app-search-card"><Search car={car} setCar={setCar} kind={kind} onKindChange={switchKind} onSubmit={find} busy={searching} disabled={!connected}/>{error && <p className="app-search-error" role="alert">{error}</p>}{result && <div className="app-car-result"><span className="app-result-plate">{result.lookup_type === 'sticker' ? '스티커 ' + result.car_id : result.plate_number}</span><p>{lots.find(item => item.lot_id === result.lot_id)?.name}</p><h2>{result.space_label || result.space_id}</h2><div className="app-result-fee"><span>{result.fee?.payment ? '결제 완료 금액' : '예상 주차요금'}</span><strong>{result.fee?money(result.fee.amount_krw):'요금 정보 없음'}</strong></div><p className="app-exit-notice">결제 후 30분 이내에 출차해 주세요.</p><p className="app-data-note">서버의 고정 샘플 요금 · 실제 청구 없음</p><button className="app-primary" onClick={() => {setLotId(result.lot_id); setFilter('all'); setTab('map');}}>지도에서 위치 보기</button></div>}</section><div className="app-example"><b>이 번호로 체험해보세요</b><p>실물: 스티커 01, 12<br/>가상: 차량번호 9012, 3456</p></div></>}
      {tab === 'chat' && <ChatPanel/>}
      {tab === 'help' && <><p className="app-eyebrow">YOUR PARKING COMPANION</p><h1>PARK:ON 이용 안내</h1><section className="app-help-card"><h2>휴대폰에 설치하기</h2><p>위쪽 앱 설치 버튼으로 안내를 확인하세요. Android와 iPhone에서 홈 화면에 추가해 사용할 수 있습니다.</p><InstallApp/></section><section className="app-help-card"><h2>도면과 검색</h2><p>1번 기본형 12면 · 2번 사선형 16면은 실물입니다. 스티커 번호 01~24로 검색하세요.</p><p>3번 순환형 12면 · 4번 L자형 10면은 가상입니다. 차량번호 뒤 4자리로 검색하세요.</p><p>초록색은 전기차, 파란색은 장애인 전용 구역입니다.</p></section><section className="app-help-card"><h2>연결과 갱신</h2><p>앱을 보고 있는 동안 {dataSource==='team'?'2.5초':'15초'}마다 조회합니다. 연결이 끊기면 이전 조회값으로 표시하며 최신 정보로 안내하지 않습니다.</p><p>현재 주차 정보와 요금은 시연용 데이터입니다.</p></section><a className="app-web-link" href="/?app=0">전체 웹 화면 열기 <Icon name="arrow"/></a></>}
    </main>
    <nav className="mobile-bottom-nav" aria-label="앱 메뉴">{nav.map(([id,label,icon]) => <button key={id} className={tab === id ? 'selected' : ''} aria-current={tab === id ? 'page' : undefined} onClick={() => {setTab(id); setError(''); window.scrollTo({top:0});}}><Icon name={icon}/><span>{label}</span></button>)}</nav>
    {space && lot && <Detail key={space.space_id} space={space} lot={lot} canQuote={connected} onClose={() => setSpace(null)} onFee={(number, lookupKind) => {setKind(lookupKind); setTab('search'); find(number, lookupKind);}}/>}
  </div>;
}
