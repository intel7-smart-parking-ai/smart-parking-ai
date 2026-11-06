import {evacuationRequired,formatKST,sourceLabel} from './uiState.js';
import {isQuoteExpired,submitMockPayment} from './payments.js';
import PaymentChooser from './PaymentChooser.jsx';
import ChatPanel from './ChatPanel.jsx';
import {useCallback, useEffect, useRef, useState} from 'react';
import {api, loadLots, lookupVehicle, dataSource, pollInterval} from './api.js';
import {Detail, Icon, Search, money} from './components.jsx';
import AirportPanel from './AirportPanel.jsx';
import ParkingLayoutMap from './ParkingLayoutMap.jsx';
import InstallApp from './InstallApp.jsx';
import './styles/mobile-app.css';

export default function MobileParkingApp({initialLotId = 'A', initialTab = 'home'}) {
  const [tab, setTab] = useState(initialTab);
  const [choosing,setChoosing]=useState(false);
  const paymentAttempts=useRef({});
  async function pay(fail,method){
    if(searchLock.current)return;if(!connected||evacuationRequired(alerts,result?.lot_id)){setError('대피 안내 또는 안전 상태 확인 중 · 결제 중단');return}searchLock.current=true;setSearching(true);setError('');
    try{const receipt=await submitMockPayment(result,method,fail,paymentAttempts.current);setResult(old=>({...old,fee:{...old.fee,payment:receipt,payment_status:'completed'}}));setChoosing(false)}
    catch(error){setError(error.message)}finally{searchLock.current=false;setSearching(false)}
  }
  const [lots, setLots] = useState([]);
  const [alerts,setAlerts]=useState([]);
  const [lotId, setLotId] = useState(initialLotId);
  const [filter, setFilter] = useState('all');
  const [kind, setKind] = useState(['C', 'D'].includes(initialLotId) ? 'plate' : 'sticker');
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
      const [data,safety]=await Promise.all([loadLots(),api('/alerts')]);setAlerts(safety);
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
    setLotId(item.lot_id); setKind(item.type === 'real' ? 'sticker' : 'plate');
    setCar(''); setResult(null); setSpace(null); setFilter('all'); setTab('map'); setError('');
    window.scrollTo({top: 0});
  }
  function switchKind(value) {setKind(value); setCar(''); setResult(null);setChoosing(false); setError('');}
  async function find(number, searchKind = kind) {
    if (searchLock.current || !connected) return;
    searchLock.current = true; setSearching(true); setResult(null); setChoosing(false); setError('');
    try {
      const data = await lookupVehicle(number, searchKind);
      setCar(number); setResult({...data,quotedAt:Date.now()});
    } catch (failure) {setError(failure.message);}
    finally {setSearching(false); searchLock.current = false;}
  }
  useEffect(()=>{if(!result)return;const expire=()=>{if(isQuoteExpired(result)){setResult(old=>old?.quotedAt===result.quotedAt?null:old);setError('요금 조회 후 2분이 지났습니다. 차량을 다시 조회해주세요.')}};const timer=window.setInterval(expire,1000);document.addEventListener('visibilitychange',expire);return()=>{window.clearInterval(timer);document.removeEventListener('visibilitychange',expire)}},[result]);
  const nav = [['home','주차장','grid'],['map','주차 지도','pin'],['search','내 차·요금','car'],['help','이용 안내','info'],['chat','주차 챗봇']];
  return <div className="mobile-parking-app">
    <header className="mobile-app-header"><a href="/?app=1" aria-label="PARK:ON 앱 홈"><span className="mobile-brand-mark" aria-hidden="true"><Icon name="car"/></span><b>PARK<span>:ON</span></b></a><div className="mobile-app-header-actions"><a className="app-switch-web" href="/?app=0">웹으로 보기 <Icon name="arrow"/></a><InstallApp/></div></header>
    <main className="mobile-app-main">
      <div className={`app-connection ${connected ? 'online' : 'offline'}`} role="status"><span><i/>{loading ? '주차 정보를 불러오는 중' : connected ? (dataSource==='demo'?'서버 연결됨 · 데모 데이터':'주차 정보 업데이트 중') : '연결 끊김 · 빈자리 재확인 필요'}</span><button onClick={refresh} aria-label="주차 정보 새로고침"><Icon name="refresh"/></button></div>
      {!loading && !connected && <div className="app-offline-notice" role="alert">{lots.length ? '현재 보이는 정보는 이전 조회값입니다. 연결이 복구되면 다시 확인해주세요.' : '서버에 연결할 수 없습니다. 네트워크와 서버 상태를 확인해주세요.'}<button onClick={refresh}>다시 연결</button></div>}
      {tab === 'home' && <>
        <section className="app-home-hero"><p>FIND YOUR PARKING</p><div><strong>{loading || !lots.length ? '—' : totalEmpty}<small>자리</small></strong><span>{connected ? '현재 조회된 주차 가능 공간' : '이전 조회의 빈자리 수'}<br/><b>실물 {lots.filter(l=>l.type==='real').length}곳 · 가상 {lots.filter(l=>l.type==='virtual').length}곳</b></span></div></section>
        <div className="app-section-heading"><h2>어디에 주차할까요?</h2><span>{updatedAt ? formatKST(updatedAt,{second:'2-digit'}) + ' 조회' : '조회 대기'}</span></div>
        <div className="app-lot-list">{lots.map((item, index) => <button key={item.lot_id} className="app-lot-card" onClick={() => openLot(item)}>
          <span className={`app-lot-number number-${index}`}>0{index+1}</span><span className="app-lot-name"><b>{item.name}</b><span className="app-lot-progress"><i style={{width: `${empty(item)/item.spaces.length*100}%`}}/></span><small>전체 {item.spaces.length}면</small></span><span className="app-free-count"><strong>{empty(item)}</strong><small>자리 가능</small><Icon name="arrow"/></span>
        </button>)}</div>
        {loading && <div className="app-placeholder">주차장 정보를 가져오고 있습니다…</div>}

      </>}
      {tab === 'map' && <><div className="app-section-heading"><h1 className="app-map-title">주차지도</h1>{lot && <div className="app-map-summary"><span className="app-map-capacity">빈자리 <strong>{empty(lot)}</strong><span> / 전체 {lot.spaces.length}면</span></span></div>}</div><div className="app-lot-switch" aria-label="지도 주차장 선택">{lots.map(item => <button key={item.lot_id} className={lotId === item.lot_id ? 'selected' : ''} onClick={() => openLot(item)} aria-pressed={lotId === item.lot_id}><span>{item.name}</span><small>{empty(item)}자리</small></button>)}</div>{lot && <>{lot.lot_id==='C'&&<AirportPanel lot={lot}/>}<ParkingLayoutMap hidePreviewControls key={lot.lot_id} lot={lot} filter={filter} setFilter={setFilter} alerts={alerts} found={result} onSpace={setSpace}/><button className="app-primary" onClick={() => {setTab('search'); setKind(lot.type === 'real' ? 'sticker' : 'plate'); setResult(null);}}>내 차 찾기</button></>}</>}
      {tab === 'search' && <><h1>내 차와 이용요금</h1><p className="app-intro">실물은 스티커로, 가상은 차량번호로 찾으세요.</p><section className="app-search-card"><Search car={car} setCar={setCar} kind={kind} onKindChange={switchKind} onSubmit={find} busy={searching} disabled={!connected}/>{error && <p className="app-search-error" role="alert">{error}</p>}{result && <div className="app-car-result"><span className="app-result-plate">{result.lookup_type === 'sticker' ? '스티커 ' + result.car_id : result.plate_number}</span><p>{lots.find(item => item.lot_id === result.lot_id)?.name}</p><h2>{result.space_id}</h2><div className="app-result-fee"><span>{result.fee?.payment ? '결제 완료 금액' : '예상 주차요금'}</span><strong>{result.fee?money(result.fee.amount_krw):'요금 정보 없음'}</strong></div>{evacuationRequired(alerts,result.lot_id)&&<p className="operation-error" role="alert">레벨 2 · 대피 안내 · 결제 중단</p>}<p className="app-exit-notice">결제 후 30분 이내에 출차해 주세요.</p><p className="app-data-note">서버 이용 시간 기준 요금 · 실제 청구 없음</p>{result.fee?.payment?.status==='completed'?<section className="receipt" role="status"><h3>모의 결제 완료</h3><p>영수증: {result.fee.payment.payment_id}</p><p>결제 후 30분 이내에 출차해 주세요.</p></section>:result.fee&&(choosing?<PaymentChooser blocked={evacuationRequired(alerts,result.lot_id)||!connected} amount={result.fee.amount_krw} busy={searching} error={error} onPay={pay} onBack={()=>setChoosing(false)}/>:<button className="app-primary" disabled={searching||!connected||evacuationRequired(alerts,result.lot_id)} onClick={()=>setChoosing(true)}>모의 결제하기</button>)}<button className="app-primary" onClick={() => {setLotId(result.lot_id); setFilter('all'); setTab('map');}}>지도에서 위치 보기</button></div>}</section><div className="app-example"><b>현재 주차 중인 차량을 찾을 수 있어요</b><p>실물 주차장은 스티커 번호, 가상 주차장은 차량번호 뒤 4자리를 입력해 주세요.</p></div></>}
      {tab === 'chat' && <ChatPanel lots={lots} alerts={alerts} voiceInput/>}
      {tab === 'help' && <><h1>PARK:ON 이용 안내</h1>
        <section className="app-help-card">
          <h2>1. 빈자리 확인하기</h2>
          <p>하단의 ‘주차 지도’에서 주차장을 선택하세요. ‘빈자리’, ‘전기차’, ‘장애인’ 버튼으로 원하는 칸을 찾을 수 있어요.</p>
        </section>
        <section className="app-help-card">
          <h2>2. 내 차와 요금 찾기</h2>
          <p>하단의 ‘내 차·요금’에서 개발원과 부산역은 스티커 번호로, 김해공항과 신세계 백화점은 차량번호 뒤 4자리로 검색하세요.</p>
          <p>검색 결과에서 주차 위치와 요금을 확인할 수 있어요. 결제는 시연용이며 실제로 청구되지 않아요.</p>
        </section>
        <section className="app-help-card">
          <h2>3. 주차 정보 다시 확인하기</h2>
          <p>연결이 끊기면 이전에 조회한 정보가 표시돼요. 연결이 돌아오면 화면 오른쪽 위의 새로고침 버튼을 눌러 주세요.</p>
        </section>
        <a className="app-web-link" href="/?app=0">전체 웹 화면 열기 <Icon name="arrow"/></a></>}
    </main>
    <nav className="mobile-bottom-nav" aria-label="앱 메뉴">{nav.map(([id,label,icon]) => <button key={id} className={tab === id ? 'selected' : ''} aria-current={tab === id ? 'page' : undefined} onClick={() => {setTab(id); setError(''); window.scrollTo({top:0});}}>{id === 'chat' ? <img className="mobile-chatbot-icon" src="/icons/mobile-chatbot.png" alt=""/> : <Icon name={icon}/>}<span>{label}</span></button>)}</nav>
    {space && lot && <Detail key={space.space_id} space={space} lot={lot} canQuote={connected} onClose={() => setSpace(null)} onFee={(number, lookupKind) => {setKind(lookupKind); setTab('search'); find(number, lookupKind);}}/>}
  </div>;
}
