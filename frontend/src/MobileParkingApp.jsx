import BrandWordmark from './BrandWordmark.jsx';
import {t,getLanguage} from './serviceLocale.js';
import {evacuationRequired,formatKST,sourceLabel} from './uiState.js';
import {isQuoteExpired,submitMockPayment} from './payments.js';
import PaymentChooser from './PaymentChooser.jsx';
import ChatPanel from './ChatPanel.jsx';
import {useCallback, useEffect, useRef, useState} from 'react';
import {api, loadLots, lookupVehicle, dataSource, pollInterval} from './api.js';
import {Detail, Icon, Search, money, ChatbotIcon} from './components.jsx';
import AirportPanel from './AirportPanel.jsx';
import ParkingLayoutMap from './ParkingLayoutMap.jsx';
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
  const lotIdRef = useRef(lotId);
  lotIdRef.current = lotId;
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
      // 보고 있는 주차장만 매번, 나머지는 30초마다 (11/10 부하 점검)
      const [data,safety]=await Promise.all([loadLots(undefined,{focusLotId:lotIdRef.current}),api('/alerts')]);setAlerts(safety);
      setLots(data); setUpdatedAt(dataSource==='team'&&data[0]?.updated_at?new Date(data[0].updated_at):new Date()); setConnected(true);
    } catch {
      setConnected(false);
    } finally {setLoading(false); refreshing.current = false;}
  }, []);
  useEffect(() => {
    document.title = t('PARK : ON · 주차장 앱');
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
  useEffect(() => {if (!refreshing.current) refresh();}, [lotId]); // eslint-disable-line react-hooks/exhaustive-deps
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
  useEffect(()=>{if(!result)return;const expire=()=>{if(isQuoteExpired(result)){setResult(old=>old?.quotedAt===result.quotedAt?null:old);setError(t('요금 조회 후 2분이 지났습니다. 차량을 다시 조회해주세요.'))}};const timer=window.setInterval(expire,1000);document.addEventListener('visibilitychange',expire);return()=>{window.clearInterval(timer);document.removeEventListener('visibilitychange',expire)}},[result]);
  const nav = [['home',t('주차장'),'grid'],['map',t('주차 지도'),'pin'],['search',t('주차요금'),'car'],['chat',t('주차 챗봇')],['help',t('이용 안내'),'info']];
  return <div className="mobile-parking-app">
    <header className="mobile-app-header">
      <a href={`/?app=1&lang=${getLanguage()}&promo=1`} aria-label="PARK:ON 시작 화면"><BrandWordmark/></a>
      <div className="mobile-app-header-actions"><a className="app-switch-web" href={`/?app=0&lang=${getLanguage()}`}>{t("웹으로 보기")} <Icon name="arrow"/></a></div>
    </header>
    <main className="mobile-app-main">
      <div className={`app-connection ${connected ? 'online' : 'offline'}`} role="status"><span><i/>{loading ? t('주차 정보를 불러오는 중') : connected ? (dataSource==='demo'?t('서버 연결됨 · 데모 데이터'):t('주차 정보 업데이트 중')) : t('연결 끊김 · 빈자리 재확인 필요')}</span><div className="app-connection-actions"><span id="mobile-chat-tools"/><button onClick={refresh} aria-label={t("주차 정보 새로고침")}><Icon name="refresh"/></button></div></div>
      {!loading && !connected && <div className="app-offline-notice" role="alert">{lots.length ? t('현재 보이는 정보는 이전 조회값입니다. 연결이 복구되면 다시 확인해주세요.') : t('서버에 연결할 수 없습니다. 네트워크와 서버 상태를 확인해주세요.')}<button onClick={refresh}>{t("다시 연결")}</button></div>}
      {tab === 'home' && <>
        <section className="app-home-hero"><p>FIND YOUR PARKING</p><div><strong>{loading || !lots.length ? '—' : totalEmpty}<small>{t("자리")}</small></strong></div></section>
        <div className="app-section-heading"><h2>{t("어디에 주차할까요?")}</h2><span>{updatedAt ? formatKST(updatedAt,{second:'2-digit'}) + t(' 조회') : t('조회 대기')}</span></div>
        <div className="app-lot-list">{lots.map((item, index) => <button key={item.lot_id} className="app-lot-card" onClick={() => openLot(item)}>
          <span className={`app-lot-number number-${index}`}>0{index+1}</span><span className="app-lot-name"><b>{item.name}</b><span className="app-lot-progress"><i style={{width: `${empty(item)/item.spaces.length*100}%`}}/></span><small>{t("전체")} {item.spaces.length}{t("면")}</small></span><span className="app-free-count"><strong>{empty(item)}</strong><small>{t("자리 가능")}</small><Icon name="arrow"/></span>
        </button>)}</div>
        {loading && <div className="app-placeholder">{t("주차장 정보를 가져오고 있습니다…")}</div>}

      </>}
      {tab === 'map' && <><div className="app-section-heading"><h1 className="app-map-title">{t("주차지도")}</h1>{lot && <div className="app-map-summary"><span className="app-map-capacity">{t("빈자리")} <strong>{empty(lot)}</strong><span> {t("/ 전체")} {lot.spaces.length}{t("면")}</span></span></div>}</div><div className="app-lot-switch" aria-label={t("지도 주차장 선택")}>{lots.map(item => <button key={item.lot_id} className={lotId === item.lot_id ? 'selected' : ''} onClick={() => openLot(item)} aria-pressed={lotId === item.lot_id}><span>{item.name}</span><small>{empty(item)}{t("자리")}</small></button>)}</div>{lot && <>{lot.lot_id==='C'&&<AirportPanel lot={lot}/>}<ParkingLayoutMap legendNearMap hidePreviewControls hideTitle key={lot.lot_id} lot={lot} filter={filter} setFilter={setFilter} alerts={alerts} found={result} onSpace={setSpace}/><button className="app-primary" onClick={() => {setTab('search'); setKind(lot.type === 'real' ? 'sticker' : 'plate'); setResult(null);}}>{t("내 차 찾기")}</button></>}</>}
      {tab === 'search' && <><h1>{t("주차요금")}</h1><section className="app-search-card"><Search car={car} setCar={setCar} kind={kind} onKindChange={switchKind} onSubmit={find} busy={searching} disabled={!connected}/>{error && <p className="app-search-error" role="alert">{error}</p>}{result && <div className="app-car-result"><span className="app-result-plate">{result.lookup_type === 'sticker' ? t('스티커 ') + result.car_id : result.plate_number}</span><p>{lots.find(item => item.lot_id === result.lot_id)?.name}</p><h2>{result.space_id}</h2><div className="app-result-fee"><span>{result.fee?.payment ? t('결제 완료 금액') : t('예상 주차요금')}</span><strong>{result.fee?money(result.fee.amount_krw):t('요금 정보 없음')}</strong></div>{evacuationRequired(alerts,result.lot_id)&&<p className="operation-error" role="alert">{t("레벨 2 · 대피 안내 · 결제 중단")}</p>}<p className="app-exit-notice">{t("결제 후 30분 이내에 출차해 주세요.")}</p><p className="app-data-note">{t("서버 이용 시간 기준 요금 · 실제 청구 없음")}</p>{result.fee?.payment?.status==='completed'?<section className="receipt" role="status"><h3>결제 완료</h3><p>{t("영수증:")} {result.fee.payment.payment_id}</p><p>{t("결제 후 30분 이내에 출차해 주세요.")}</p></section>:result.fee&&(choosing?<PaymentChooser blocked={evacuationRequired(alerts,result.lot_id)||!connected} amount={result.fee.amount_krw} busy={searching} error={error} onPay={pay} onBack={()=>setChoosing(false)}/>:<button className="app-primary" disabled={searching||!connected||evacuationRequired(alerts,result.lot_id)} onClick={()=>setChoosing(true)}>{t("결제하기")}</button>)}<button className="app-primary" onClick={() => {setLotId(result.lot_id); setFilter('all'); setTab('map');}}>{t("지도에서 위치 보기")}</button></div>}</section></>}
      {tab === 'chat' && <ChatPanel lots={lots} alerts={alerts} lotId={lotId} voiceInput appAppearance connected={connected}/>}
      {tab === 'help' && <><h1 className="app-help-title">{t("PARK : ON 이용 안내").split("PARK : ON").map((part,index)=><span key={index}>{index>0&&<BrandWordmark/>}{part}</span>)}</h1>
        <section className="app-help-card">
          <h2>{t("1. 빈자리 확인하기")}</h2>
          <p>{t("하단의 ‘주차 지도’에서 주차장을 선택하세요.\n‘빈자리’, ‘전기차’, ‘장애인’ 버튼으로 원하는 칸을 찾을 수 있어요.")}</p>
        </section>
        <section className="app-help-card">
          <h2>{t("2. 내 차와 요금 찾기")}</h2>
          <p>{t("하단의 ‘주차요금’에서 개발원과 부산역은 스티커 번호로 검색하세요.\n김해공항과 신세계 백화점은 차량번호 뒤 4자리로 검색하세요.")}</p>
          <p>{t("검색 결과에서 주차 위치와 요금을 확인하고, 출차 전에 주차요금을 미리 정산할 수 있어요.")}</p>
        </section>
        <section className="app-help-card">
          <h2>{t("3. 주차 정보 다시 확인하기")}</h2>
          <p>{t("연결이 끊기면 이전에 조회한 정보가 표시돼요.\n연결이 돌아오면 화면 오른쪽 위의 새로고침 버튼을 눌러 주세요.")}</p>
        </section>
        </>}
    </main>
    <nav className="mobile-bottom-nav" aria-label={t("앱 메뉴")}>{nav.map(([id,label,icon]) => <button key={id} className={tab === id ? 'selected' : ''} aria-current={tab === id ? 'page' : undefined} onClick={() => {setTab(id); setError(''); window.scrollTo({top:0});}}>{id === 'chat' ? <ChatbotIcon/> : <Icon name={icon}/>}<span>{label}</span></button>)}</nav>
    {space && lot && <Detail showDetails={Boolean(result?.session_id && result.session_id===space.session_id && result.lot_id===lot.lot_id && result.space_id===space.space_id)} key={space.space_id} space={space} lot={lot} canQuote={connected} onClose={() => setSpace(null)} onFee={(number, lookupKind) => {setKind(lookupKind); setTab('search'); find(number, lookupKind);}}/>}
  </div>;
}
