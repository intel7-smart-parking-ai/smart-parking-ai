import PresentationDemo from './PresentationDemo.jsx';
import BrandWordmark from './BrandWordmark.jsx';
import {t,getLanguage} from './serviceLocale.js';
import EntryVehiclePhoto from './EntryVehiclePhoto.jsx';
import {ParkingExtras, AdminCalls} from './ParkingExtras.jsx';
import AirportPanel from './AirportPanel.jsx';
import ObservationPanel from './ObservationPanel.jsx';
import {evacuationRequired,currentKSTMonth,formatKST} from './uiState.js';
import SessionHistoryPanel from './SessionHistoryPanel.jsx';
import {TablePagination,useTablePage} from './TablePagination.jsx';
import SimulationPanel from './SimulationPanel.jsx';
import ChatPanel from './ChatPanel.jsx';
import RevenuePanel from './RevenuePanel.jsx';
import MobileParkingApp from './MobileParkingApp.jsx';
import ParkingWelcome from './ParkingWelcome.jsx';
import ParkingLayoutMap from './ParkingLayoutMap.jsx';
import {submitMockPayment,isQuoteExpired,staleQuoteCodes} from './payments.js';
import PaymentChooser, {paymentMethods} from './PaymentChooser.jsx';
import {useCallback,useEffect,useRef,useState} from 'react';
import {api,loadLots,dataSource,lookupVehicle,pollInterval} from './api.js';
const isDemo=dataSource==='demo';
import {Icon,Badge,Detail,Search,money,time,ChatbotIcon} from './components.jsx';
const seedFilters={kind:'month',period:currentKSTMonth(),lot_id:''};
const idleTimeoutMs = 30_000;
const activityEvents = ['pointerdown', 'keydown', 'input', 'wheel', 'touchstart'];

export default function App() {
 const demo = new URLSearchParams(window.location.search).get('presentation');
 return !window.location.pathname.startsWith('/admin/') && ['C','D'].includes(demo)
  ? <PresentationDemo lotId={demo}/> : <ParkingApp/>;
}

function ParkingApp() {
 useEffect(()=>{document.documentElement.lang=getLanguage()},[]);
 const params = new URLSearchParams(window.location.search);
 const [started, setStarted] = useState(() => params.get('screen') === 'map');
 const [showWelcome, setShowWelcome] = useState(false);
 const admin = window.location.pathname.startsWith('/admin/');
 const mode = params.get('app');
 const phone = /Android.*Mobile|iPhone|iPod|Windows Phone/i.test(navigator.userAgent) ||
  navigator.userAgentData?.mobile === true;
 const standalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
 const appMode = mode === '1' || mode === 'user' || (mode !== '0' && (standalone || (started && phone)));
 const sessionActive = !admin && !showWelcome && (started || appMode);

 useEffect(() => {
  if (!sessionActive) return;
  let timer;
  let deadline = Date.now() + idleTimeoutMs;

  function expire() {
   window.clearTimeout(timer);
   const url = new URL(window.location.href);
   for (const key of ['screen', 'lot', 'page']) url.searchParams.delete(key);
   url.hash = '';
   window.history.replaceState(null, '', url);
   setStarted(false);
   setShowWelcome(true);
   window.scrollTo(0, 0);
  }

  function schedule() {
   window.clearTimeout(timer);
   timer = window.setTimeout(expire, Math.max(0, deadline - Date.now()));
  }

  function onActivity(event) {
   if (!event.isTrusted) return;
   if (Date.now() >= deadline) {
    if (event.cancelable) event.preventDefault();
    event.stopPropagation();
    expire();
    return;
   }
   deadline = Date.now() + idleTimeoutMs;
   schedule();
  }

  function onVisibilityChange() {
   if (!document.hidden && Date.now() >= deadline) expire();
  }

  schedule();
  for (const event of activityEvents) window.addEventListener(event, onActivity, true);
  document.addEventListener('visibilitychange', onVisibilityChange);
  return () => {
   window.clearTimeout(timer);
   for (const event of activityEvents) window.removeEventListener(event, onActivity, true);
   document.removeEventListener('visibilitychange', onVisibilityChange);
  };
 }, [sessionActive]);

 if (admin) return <Dashboard admin/>;
 if (sessionActive && appMode) {
  const initialTab = started && params.get('page') === 'chat' ? 'chat' : started && ['find', 'fee'].includes(params.get('page')) ? 'search' : 'home';
  return <MobileParkingApp initialLotId={params.get('lot') || 'A'} initialTab={initialTab}/>;
 }
 if (sessionActive) return <Dashboard/>;
 return <ParkingWelcome onStart={(lot, page, language) => {
  const url = new URL(window.location.href);
  url.searchParams.set('lot', lot);
  url.searchParams.set('screen', 'map');
  url.searchParams.set('page', page || 'parking');
  url.searchParams.set('lang', language || 'ko');
  window.location.assign(url.toString());
 }}/>;
}

function RailDrawer({children}) {
 const [pinned,setPinned]=useState(false);
 const [hovered,setHovered]=useState(false);
 const touch=useRef(null);
 const open=pinned||hovered;

 return <div className="rail-drawer" data-open={open}
  onPointerEnter={event=>{if(event.pointerType==='mouse')setHovered(true)}}
  onPointerLeave={()=>setHovered(false)}
  onTouchStart={event=>{touch.current=event.touches[0].clientX}}
  onTouchCancel={()=>{touch.current=null}}
  onTouchEnd={event=>{
   if(touch.current===null)return;
   const distance=event.changedTouches[0].clientX-touch.current;
   touch.current=null;
   if(Math.abs(distance)<24)return;
   setPinned(distance>0);
   setHovered(false);
  }}
  onKeyDown={event=>{if(event.key==='Escape'){setPinned(false);setHovered(false);event.currentTarget.querySelector('button').focus()}}}>
  <button type="button" className="rail-drawer-toggle" aria-label={pinned?t('왼쪽 메뉴 접기'):t('왼쪽 메뉴 펼치기')} aria-expanded={open} aria-controls="web-side-menu"
   onClick={()=>{setPinned(!pinned);setHovered(false)}}>{pinned?'‹':'›'}</button>
  <aside id="web-side-menu" className="icon-rail" inert={!open}>{children}</aside>
 </div>;
}

function Dashboard({admin=false}){
 const [theme,setTheme]=useState(()=>localStorage.getItem('parkon-react-theme')||'light');
 const mode=admin?'admin':'user';
 const [page,setPage]=useState(()=>{
  const requested=new URLSearchParams(window.location.search).get('page');
  const pages=admin?['parking','payments','revenue','alerts']:['parking','find','fee','chat'];
  return pages.includes(requested)?requested:'parking';
 }),[lotId,setLotId]=useState(()=>{const id=new URLSearchParams(window.location.search).get('lot');return ['A','B','C','D'].includes(id)?id:'A'}),[filter,setFilter]=useState('all');
 useEffect(()=>{if(admin){const url=new URL(window.location.href);url.searchParams.set('lot',lotId);window.history.replaceState(null,'',url)}},[admin,lotId]);
 const [data,setData]=useState({lots:[],policies:[],payments:[],alerts:[],sessions:[],report:null});
 const [reportFilters,setReportFilters]=useState(seedFilters),[paymentFilters,setPaymentFilters]=useState({lot_id:'',status:'',date:''}),[alertFilters,setAlertFilters]=useState({lot_id:'',status:''});
 const [lookupKind,setLookupKind]=useState(()=>['C','D'].includes(lotId)?'plate':'sticker');
 const [car,setCar]=useState(''),[invoice,setInvoice]=useState(null),[space,setSpace]=useState(null),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState(''),[refreshError,setRefreshError]=useState(''),[toast,setToast]=useState('');
 const paymentKeys=useRef({}),invoiceRef=useRef(null),busyRef=useRef(false),refreshing=useRef(false),refreshSequence=useRef(0);
 invoiceRef.current=invoice;busyRef.current=busy;
 const refresh=useCallback(async()=>{
  const sequence=++refreshSequence.current;
  const reportQuery=new URLSearchParams({[reportFilters.kind]:reportFilters.period,...(reportFilters.lot_id?{lot_id:reportFilters.lot_id}:{})});
  const query=f=>new URLSearchParams(Object.fromEntries(Object.entries(f).filter(([,value])=>value)));
  const lots=await loadLots();
  const errors={};
  const optional=async(key,fn,fallback)=>{try{return await fn()}catch(error){if(isDemo)throw error;errors[key]=error.message;return fallback}};
  const [policies,payments,alerts,sessions,report,alertRows]=await Promise.all([
    optional('policies',()=>api('/rate-policies'),[]),
    mode==='admin'&&page==='payments'?optional('payments',async()=>(await api('/mock-payments?'+query({...paymentFilters,limit:paymentFilters.date?1000:200}))).slice(0,paymentFilters.date?1000:200),[]):Promise.resolve([]),
    optional('alerts',()=>api('/alerts'),[]),
    mode==='admin'?optional('sessions',()=>api('/sessions?status=active'),[]):Promise.resolve([]),
    mode==='admin'&&page==='revenue'?optional('revenue',()=>api('/reports/revenue?'+reportQuery),null):Promise.resolve(null),
    optional('alerts',()=>api('/alerts?'+query(alertFilters)),[])]);
  if(sequence===refreshSequence.current)setData({lots,policies,payments,alerts,sessions,report,alertRows,featureErrors:errors});
 },[mode,page,reportFilters,paymentFilters,alertFilters]);
 useEffect(()=>{let active=true;setLoading(true);refresh().then(()=>{if(active)setRefreshError('')}).catch(e=>{if(active)setRefreshError(e.message)}).finally(()=>{if(active)setLoading(false)});return()=>{active=false}},[refresh]);
 useEffect(()=>{document.documentElement.dataset.theme=theme;localStorage.setItem('parkon-react-theme',theme)},[theme]);
 useEffect(()=>{if(!toast)return;const id=setTimeout(()=>setToast(''),4000);return()=>clearTimeout(id)},[toast]);
 useEffect(()=>{
  let active=true;
  const id=setInterval(async()=>{
   if(busyRef.current||refreshing.current||document.hidden||document.querySelector('dialog[open]'))return;
   refreshing.current=true;
   try{await refresh();if(active)setRefreshError('')}
   catch(e){if(active)setRefreshError(e.message)}
   finally{refreshing.current=false}
  },pollInterval);
  return()=>{active=false;clearInterval(id)};
 },[refresh]);
 useEffect(()=>{
  const expire=()=>{const current=invoiceRef.current;if(!busyRef.current&&isQuoteExpired(current)){delete paymentKeys.current[current.session_id];setInvoice(old=>old?.quotedAt===current.quotedAt?null:old);setError(t('요금 조회 후 2분이 지나 화면을 초기화했습니다. 차량을 다시 조회해주세요.'))}};
  const id=window.setInterval(expire,1000);document.addEventListener('visibilitychange',expire);return()=>{window.clearInterval(id);document.removeEventListener('visibilitychange',expire)};
 },[]);
 async function run(fn,message){if(busyRef.current)return;busyRef.current=true;setBusy(true);setError('');try{await fn();await refresh();setRefreshError('');if(invoiceRef.current?.fee){const fee=await api(`/sessions/${invoiceRef.current.session_id}/fee`);setInvoice(old=>old?{...old,fee}:old)}if(message)setToast(message)}catch(e){setError(e.message)}finally{busyRef.current=false;setBusy(false)}}
 async function find(number,kind=lookupKind){setCar(number.trim());setInvoice(null);setError('');await run(async()=>{const result=await lookupVehicle(number,kind);setInvoice({...result,quotedAt:Date.now()})})}
 async function pay(fail,paymentMethod){
  if(busyRef.current||!invoiceRef.current)return;
  const current=invoiceRef.current;
  if(evacuationRequired(data.alerts,current.lot_id)||data.featureErrors?.alerts){setError(t('대피 안내 또는 안전 상태 확인 중에는 결제할 수 없습니다.'));return}
  busyRef.current=true;setBusy(true);setError('');
  try{
   const payment=await submitMockPayment(current,paymentMethod,fail,paymentKeys.current);
   setInvoice(old=>old?.session_id===current.session_id?{...old,fee:{...old.fee,payment}}:old);
   setToast(t('결제가 완료되었습니다. 영수증을 확인해주세요.'));
   try{await refresh()}catch{setToast(t('결제는 완료되었습니다. 결제 내역 갱신은 새로고침해주세요.'))}
  }catch(e){if(staleQuoteCodes.includes(e.code)){delete paymentKeys.current[current.session_id];setInvoice(old=>old?.session_id===current.session_id?null:old);setError(t('견적이 만료되었거나 변경되었습니다. 차량을 다시 조회해주세요.'))}else setError(e.message)}finally{busyRef.current=false;setBusy(false)}
 }
 function cancel(payment){run(async()=>{await api(`/mock-payments/${payment.payment_id}/cancel`,{method:'POST'});delete paymentKeys.current[payment.session_id]},'취소와 서버 순매출이 반영되었습니다.')}
 function alertAction(id,action){run(()=>api(`/alerts/${id}/${action}`,{method:'POST'}),'경보 상태가 저장되었습니다.')}
 function fire(){run(()=>api('/demo/alerts',{method:'POST',body:{lot_id:lotId}}),'선택한 주차장에 LED 경보가 발생했습니다.')}
 const lot=data.lots.find(l=>l.lot_id===lotId)||data.lots[0],activeAlerts=data.alerts.filter(a=>a.status!=='resolved');
 const menus=mode==='admin'?[['parking','통합 관제','grid'],['payments','입출차 · 결제','wallet'],['revenue','매출 통계','chart'],['alerts','안전 현황','bell']]:[['parking',t('주차장 현황'),'grid'],['find',t('내 차 찾기'),'car'],['fee',t('요금 결제'),'wallet'],['alerts',t('안전 안내'),'bell'],['chat',t('주차 챗봇'),'info']];
 const lotName=id=>data.lots.find(l=>l.lot_id===id)?.name||id;
 const navigate=id=>{setPage(id);setError('');if(admin){const url=new URL(window.location.href);url.searchParams.set('page',id);window.history.replaceState(null,'',url)}};
 const search=<Search car={car} setCar={setCar} onSubmit={find} busy={busy} disabled={false} kind={lookupKind} onKindChange={kind=>{setLookupKind(kind);setCar('');setInvoice(null);setError('')}}/>;
 const found=invoice&&<div className={"found-card"+(['C','D'].includes(invoice.lot_id)?' with-entry-photo':'')}><Icon name="pin"/><div><span className="full-plate-number">{invoice.lookup_type==='sticker'?t('스티커 ')+invoice.car_id:invoice.plate_number||invoice.vehicle_number}</span><span>{t("차량의 현재 주차 위치")}</span><strong>{lotName(invoice.lot_id)} · {invoice.space_id}</strong>{invoice.fee_notice&&<p className="fee-note">{invoice.fee_notice}</p>}{invoice.fee&&<div className="found-fee"><span>{invoice.fee.payment?'결제 완료':t('예상 주차요금')}</span><b>{money(invoice.fee.amount_krw)}</b><small>{t("서버 이용 시간 기준 요금")}</small></div>}</div>{['C','D'].includes(invoice.lot_id)&&<EntryVehiclePhoto plate={invoice.plate_number||invoice.vehicle_number}/>}<button aria-label={t("지도에서 보기")} onClick={()=>{setLotId(invoice.lot_id);setPage('parking');setFilter('all')}}><Icon name="arrow"/></button></div>;
 if(!lot)return <div className="initial-loading">PARK : ON<span>{error||t('Docker API에 연결하는 중…')}</span>{error&&<button className="button primary" onClick={()=>location.reload()}>{t("다시 연결")}</button>}</div>;
 const occupied=lot.spaces.filter(s=>s.status==='occupied').length;
 return <><header className="app-header"><a className="brand" href={`/?app=0&lang=${getLanguage()}`} aria-label="PARK : ON 시작화면"><BrandWordmark/></a><div className="header-tools"><div className="theme-switch" aria-label={t("화면 밝기")}>{[['light',t('☀ 밝게')],['dark',t('☾ 어둡게')]].map(([id,label])=><button key={id} onClick={()=>setTheme(id)} className={theme===id?'selected':''} aria-pressed={theme===id}>{label}</button>)}</div><span className="demo-label"><i/>{isDemo?t('목업 시연'):t('팀 API')}</span>{admin?<><span className="badge real">관리자 전용</span><form action="/auth/logout" method="post"><button className="text-button" type="submit">로그아웃</button></form><a className="mobile-app-entry" href="/">사용자 페이지</a></>:<span className="badge real">{t("사용자 서비스")}</span>}<a className="mobile-app-entry" href={`/?app=1&lang=${getLanguage()}`}>{t("모바일 앱")}</a></div></header>
 <RailDrawer><nav aria-label={t("주요 메뉴")}>{menus.map(([id,label,icon])=><button key={id} className={`rail-button ${page===id?'active':''}`} aria-label={label} title={label} onClick={()=>navigate(id)}>{id==='chat'?<ChatbotIcon/>:<Icon name={icon}/>}</button>)}</nav><div className="rail-bottom"><span className="vertical-label">REACT / FASTAPI / SQLITE</span><span className="rail-version">v0.3</span></div></RailDrawer>

 <div className="main-shell"><main><div className="page-heading"><div><h1 className="parking-page-title">{page==='parking'?<>{lot.name}</>:menus.find(m=>m[0]===page)?.[1]}</h1></div>{page==='parking'&&<div className="location-total"><span>{t("전체 주차 현황")}</span><strong>{data.lots.flatMap(l=>l.spaces).filter(s=>s.status==='occupied').length}<small> / {data.lots.flatMap(l=>l.spaces).length}{t("면 주차 중")}</small></strong></div>}<button className="refresh-button" aria-label={t("새로고침")} disabled={busy||loading} onClick={()=>run(async()=>{},t('API 데이터를 새로 불러왔습니다.'))}><Icon name="refresh"/></button></div><nav className="service-nav" aria-label={t("서비스 메뉴")}>{menus.map(([id,label,icon])=><button key={id} className={page===id?'selected':''} onClick={()=>navigate(id)}>{id==='chat'?<ChatbotIcon/>:<Icon name={icon}/>} {label}{id==='alerts'&&activeAlerts.length>0&&<span>{activeAlerts.length}</span>}</button>)}</nav>
 {data.featureErrors?.[page]&&<div className="operation-error" role="status">{data.featureErrors[page]} {t("· 해당 기능은 팀 API 연결 대기 중입니다.")}</div>}{(error||refreshError)&&<div className="operation-error" role="alert">{error||refreshError}<button onClick={()=>run(async()=>{})}>{t("다시 불러오기")}</button></div>}
 {admin&&<FireAlertPopup alerts={activeAlerts} lotName={lotName} onOpen={()=>navigate('alerts')}/>}
 {activeAlerts.some(a=>a.lot_id===lotId)&&<button className="safety-banner" onClick={()=>navigate('alerts')}><Icon name="bell"/>{lot.name} · {evacuationRequired(data.alerts,lotId)?t('레벨 2 대피 안내 · 결제 중단'):t('화재 경보 · 위치와 상태 확인')}<Icon name="arrow"/></button>}
 {mode==='admin'&&page==='parking'&&<ObservationPanel lot={lot} filter={filter} setFilter={setFilter}/>}
 {page==='parking'&&mode==='admin'&&(lot.simulation_enabled||lot.capabilities?.simulation)&&lot.type==='virtual'&&<SimulationPanel key={lot.lot_id} lot={lot} onRefresh={refresh}/>}
 {page==='parking'&&<ParkingExtras lot={lot} lots={data.lots} alerts={data.alerts} onSelect={setLotId} syncStatus={<span className="report-scope">{isDemo?t('시연 데이터'):t('팀 API')} · {pollInterval/1000}{t("초 갱신 · 관측 시각")} {time(lot.updated_at)}</span>}/>}

 {page==='parking'&&<div className="parking-workspace"><ParkingLayoutMap admin={admin} hidePreviewControls key={lot.lot_id} lot={lot} filter={filter} setFilter={setFilter} alerts={data.alerts} found={invoice} onSpace={setSpace}/><aside className="right-column"><section className="occupancy-panel"><div className="section-kicker">PARKING STATUS<span>{t("현황")}</span></div><div className="occupancy-ring" style={{'--percent':`${occupied/lot.spaces.length*100}%`}}><div><strong>{Math.round(occupied/lot.spaces.length*100)}<small>%</small></strong><span>{t("현재 점유율")} · {occupied}/{lot.spaces.length}면</span></div></div><div className="status-numbers">{[['empty',t('빈자리')],['occupied',t('주차 중')],['uncertain',t('확인 필요')],['unavailable',t('확인 불가')]].map(([id,label])=><div key={id}><i className={`legend-dot ${id}`}/><span>{label}</span><b>{lot.spaces.filter(s=>s.status===id).length}</b></div>)}</div></section><article className="panel quick-find"><div className="card-heading"><h3>{t("내 차 찾기")}</h3><Icon name="search"/></div><p>{t("차량에 맞는 조회 방식을 선택해 주세요.")}</p>{search}{found}<button className="button white full quick-fee-link" onClick={()=>navigate('fee')}>{t("주차요금 확인")}</button></article>{mode==='admin'&&<article className="panel"><h3>LED 화재 경보</h3><p className="muted">실제 카메라 연결 전 시연입니다.</p><button className="button danger full" disabled={busy} onClick={fire}>LED 경보 시연</button></article>}</aside></div>}

 {page==='chat'&&<ChatPanel lots={data.lots} alerts={data.alerts} voiceInput appAppearance connected={!loading&&!error}/>}
 {page==='find'&&<section className="search-page"><article className="panel search-large"><span className="large-symbol"><Icon name="car"/></span><h2>{t("주차장에 맞는 번호로 내 차 찾기")}</h2><p>{t("실물 1·2번은 스티커 번호, 가상 3·4번은 차량번호 뒤 4자리로 조회하세요.")}</p>{search}{found}{invoice&&<button className="button primary full quick-fee-link" onClick={()=>navigate('fee')}>{t("요금 확인하기")}</button>}</article></section>}
 {page==='fee'&&<FeePanel key={invoice?.session_id||'empty'} selectedLotId={lotId} {...{data,invoice,search,found,busy,error,pay,navigate}}/>}
 {page==='payments'&&<><PaymentsPanel {...{data,mode,busy,cancel,lotName,paymentFilters,setPaymentFilters}}/>{mode==='admin'&&<><SessionHistoryPanel lots={data.lots}/><AdminCalls/></>}</>}
 {page==='revenue'&&mode==='admin'&&!data.featureErrors?.revenue&&<RevenuePanel report={data.report} lots={data.lots} filters={reportFilters} onFilter={setReportFilters} loading={loading} onOpenPayments={(date,lot_id)=>{setPaymentFilters({date,lot_id,status:""});navigate("payments")}}/>}
 {page==='alerts'&&<AlertsPanel {...{data:{...data,alerts:data.alertRows||[]},mode,busy,fire,alertAction,lotName,alertFilters,setAlertFilters}}/>}
 {page==='parking'&&!admin&&lot.lot_id==='C'&&<AirportPanel lot={lot}/>}
 {page!=='revenue'&&<aside className="locations locations-below-map" aria-label={t("주차장 목록")}><div className="locations-list">{data.lots.map(l=>{const taken=l.spaces.filter(s=>s.status==='occupied').length,empty=l.spaces.filter(s=>s.status==='empty').length,unknown=l.spaces.length-taken-empty;return <button key={l.lot_id} className={`location ${lotId===l.lot_id?'selected':''}`} aria-pressed={lotId===l.lot_id} onClick={()=>{setLotId(l.lot_id);setLookupKind(l.type==='real'?'sticker':'plate');setCar('');setInvoice(null);navigate('parking');setFilter('all')}}><span className="location-info"><span className="location-title">{l.name}</span><span className="location-meta"><b>{empty}{t("자리 가능")}</b><span>{taken}/{l.spaces.length}면 · {Math.round(taken/l.spaces.length*100)}{t("% 점유")}{unknown>0&&<> · {t("확인 필요")} {unknown}면</>}</span></span></span></button>})}</div></aside>}
 </main></div>
 {space&&<Detail showDetails={admin || Boolean(invoice?.session_id && invoice.session_id===space.session_id && invoice.lot_id===lot.lot_id && invoice.space_id===space.space_id)} key={space.space_id} {...{space,lot}} onClose={()=>setSpace(null)} canQuote={true} onFee={(number,kind)=>{setLookupKind(kind);navigate('fee');find(number,kind)}}/>}
 {toast&&<div id="toast" className="show" role="status">{toast}</div>}</>;
}
function FeePanel({data,invoice,search,found,busy,error,pay,navigate,selectedLotId}){const [allPolicies,setAllPolicies]=useState(false);const blocked=evacuationRequired(data.alerts,invoice?.lot_id)||Boolean(data.featureErrors?.alerts);const [choosing,setChoosing]=useState(false);const fee=invoice?.fee,payment=fee?.payment||(fee?.payment_status==='completed'?{status:'completed',amount_krw:fee.amount_krw}:null);const receiptRef=useRef(null);useEffect(()=>{if(payment?.payment_id){receiptRef.current?.scrollIntoView({behavior:'smooth',block:'center'});receiptRef.current?.focus({preventScroll:true})}},[payment?.payment_id]);return <><div className="operation-intro"><div><h2>{t("주차요금 조회 · 결제")}</h2><p>{t("서버 견적 확인 → 결제 → DB 저장 → 영수증")}</p></div><span className="badge real">{t("HTTP API 연결")}</span></div><section className="fee-workspace"><article className="panel fee-main"><div className="panel-header"><div><h2>{t("내 차량의 이용요금")}</h2><p>{isDemo?t('실물: 스티커 10~99 / 가상: 뒤 4자리'):t('스티커 10~99 · 두 자리 입력 · 예: 10, 99')}</p></div></div><div className="fee-body">{search}{found}{fee?<><div className="session-detail"><div><span>{t("차량번호")}</span><b>{invoice.lookup_type==='sticker'?t('스티커 ')+invoice.car_id:invoice.plate_number||invoice.vehicle_number}</b></div><div><span>{t("입차 시각")}</span><b>{time(invoice.entry_at)}</b></div><div><span>{t("이용 시간")}</span><b>{fee.duration_minutes}{t("분")}</b></div><div><span>{t("기본요금 ·")} {fee.policy?.base_minutes ?? '—'}{t("분")}</span><b>{money(fee.base_amount_krw)}</b></div><div><span>{t("추가요금 ·")} {fee.extra_units}{t("회")}</span><b>{money(fee.extra_amount_krw)}</b></div>{fee.cap_discount_krw>0&&<div><span>{t("일일 상한 적용")}</span><b>−{money(fee.cap_discount_krw)}</b></div>}<div><span>{t("결제 금액")}</span><strong>{money(fee.amount_krw)}</strong></div></div><p className="fee-note">{t("미결제 요금 조회는 2분 후 초기화됩니다. 만료되면 차량을 다시 조회해주세요.")}</p><p className="fee-note"><strong>{t("결제 후 30분 이내에 출차해 주세요.")}</strong></p><p className="fee-note">{isDemo?t('서버가 이용 시간과 요금 정책으로 계산한 견적입니다.'):t('팀 서버가 계산한 금액을 그대로 표시합니다.')}</p>{blocked&&!payment&&<p className="operation-error" role="alert">{t("대피 안내 또는 안전 상태 확인 중 · 결제를 중단합니다.")}</p>}{payment?<section className="receipt payment-success" ref={receiptRef} tabIndex={-1} role="status" aria-live="polite"><div><Icon name="check"/><strong>{t("결제가 완료되었습니다")}</strong></div><p><strong>{t("결제 후 30분 이내에 출차해 주세요.")}</strong></p><p>{t("실제 청구는 없습니다. 결제와 출차는 별도 처리됩니다.")}</p><dl><div><dt>{t("영수증 번호")}</dt><dd>{payment.payment_id||t('서버 결제 완료 상태 확인')}</dd></div><div><dt>{t("결제 수단")}</dt><dd>{paymentMethods[payment.payment_method]||t('서버 응답 미제공')}</dd></div><div><dt>{t("결제 시각")}</dt><dd>{time(payment.created_at)}</dd></div><div><dt>{t("결제 금액")}</dt><dd>{money(payment.amount_krw)}</dd></div></dl></section>:choosing?<PaymentChooser blocked={blocked} amount={fee.amount_krw} busy={busy} error={error} onPay={pay} onBack={()=>setChoosing(false)}/>:<button className="button primary full" disabled={busy||blocked} onClick={()=>setChoosing(true)}>{t("요금 결제하기 ·")} {money(fee.amount_krw)}</button>}</>:<div className="empty-state compact"><h3>{t("차량 번호를 조회해주세요")}</h3><p>{invoice?.fee_notice||t('입력한 차량의 API 견적이 표시됩니다.')}</p></div>}</div></article><aside className="fee-aside"><article className="panel policy-panel"><h3>{allPolicies?t("전체 주차장 요금 정책"):t("주차요금 안내")}</h3><p>{t("화면은 API 응답만 표시하며 요금을 계산하지 않습니다.")}</p>{data.policies.filter(p=>allPolicies||p.lot_id===(invoice?.lot_id||selectedLotId)).map(p=><div className="policy-row" key={p.lot_id}><strong>{data.lots.find(lot=>lot.lot_id===p.lot_id)?.name||({A:t("개발원 주차장"),B:t("부산역 주차장"),C:t("김해공항 주차장"),D:t("신세계 백화점 주차장")}[p.lot_id])||p.lot_id}</strong><span>{t("기본")} {p.base_minutes}{t("분")} {money(p.base_fee)}<br/>{p.unit_minutes}{t("분당 +")}{money(p.extra_fee)}<br/>{t("일일 최대")} {money(p.daily_cap)}</span></div>)}<button type="button" className="button white full" style={{marginTop:16}} aria-expanded={allPolicies} onClick={()=>setAllPolicies(value=>!value)}>{allPolicies?t("선택 주차장 요금만 보기"):t("전체 요금 정책 보기")}</button></article><article className="panel flow-note"><h3>{t("서버 저장 확인")}</h3><p>{t("결제와 경보는 SQLite에 저장되어 브라우저 및 컨테이너를 다시 시작해도 유지됩니다.")}</p></article></aside></section></>}
function Filters({value,onChange,lots,statuses,includeDate=false,submit=t('조회')}){return <form className="operation-filters" onSubmit={e=>{e.preventDefault();const form=new FormData(e.currentTarget);onChange(Object.fromEntries(form))}}><label>{t("주차장")}<select name="lot_id" defaultValue={value.lot_id}><option value="">{t("전체 주차장")}</option>{lots.map(l=><option key={l.lot_id} value={l.lot_id}>{l.name}</option>)}</select></label><label>{t("상태")}<select name="status" defaultValue={value.status}><option value="">{t("전체 상태")}</option>{statuses.map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>{includeDate&&<label>결제일<input type="date" name="date" defaultValue={value.date}/></label>}<button className="button primary">{submit}</button></form>}
function PaymentsPanel({data,mode,busy,cancel,lotName,paymentFilters,setPaymentFilters}){
 const payments=useTablePage(data.payments);
 const sessions=useTablePage(data.sessions);
 return <><div className="operation-intro"><div><h2>{mode==='admin'?'결제 · 입출차 관리':'결제 내역'}</h2></div><span className="badge real">{paymentFilters.date?paymentFilters.date+" · ":"최근 "}{data.payments.length}건</span></div><Filters key={JSON.stringify(paymentFilters)} includeDate value={paymentFilters} onChange={filters=>{payments.setPage(1);setPaymentFilters(filters)}} lots={data.lots} statuses={[['completed','결제 완료'],['canceled','취소 완료']]}/><section className="panel table-panel"><TablePagination label="결제 목록" pagination={payments}/><div className="table-scroll"><table><thead><tr><th>결제 번호 / 시각</th><th>차량</th><th>{t("주차장")}</th><th>금액 / 결제 수단</th><th>{t("상태")}</th><th>처리</th></tr></thead><tbody>{payments.rows.map(p=><tr key={p.payment_id}><td>{p.payment_id}<small className="table-sub">{time(p.created_at)}</small></td><td>{p.plate_number||p.car_id||"미식별"}</td><td>{lotName(p.lot_id)}</td><td>{money(p.amount_krw)}<small className="table-sub">{paymentMethods[p.payment_method]||t('신용카드')}</small></td><td><span className={`badge ${p.status==='completed'?'real':'neutral'}`}>{p.status==='completed'?'결제 완료':'취소 완료'}</span></td><td>{mode==='admin'&&p.status==='completed'?<button className="text-button" disabled={busy} onClick={()=>cancel(p)}>결제 취소</button>:'—'}</td></tr>)}{!data.payments.length&&<tr><td colSpan="6">조회 조건에 맞는 결제가 없습니다.</td></tr>}</tbody></table></div></section>{mode==='admin'&&<section className="panel table-panel"><div className="panel-header"><h2>현재 입차 세션</h2></div><TablePagination label="현재 입차 세션" pagination={sessions}/><div className="table-scroll"><table><thead><tr><th>차량</th><th>주차장 / 주차칸</th><th>{t("입차 시각")}</th></tr></thead><tbody>{sessions.rows.map(s=><tr key={s.session_id}><td>{s.plate_number||s.car_id||"미식별"}</td><td>{lotName(s.lot_id)} / {s.space_id}</td><td>{time(s.entry_at)}</td></tr>)}</tbody></table></div></section>}</>}

const fireAlertTypes = new Set(['mock_fire_led','mock_fire_led_observed','fire','fire_detected']);

function FireAlertPopup({alerts,lotName,onOpen}) {
 const [dismissed,setDismissed]=useState([]);
 const alert=alerts.find(item=>item.alert_id&&fireAlertTypes.has(item.type)&&!dismissed.includes(item.alert_id));
 if(!alert)return null;
 const mock=alert.is_mock||alert.type.startsWith('mock_')||alert.source==='simulation';
 const zone=alert.zone||alert.space_id?.split('-')[0];
 const dismiss=()=>setDismissed(previous=>[...previous,alert.alert_id]);
 return <aside className="admin-fire-popup" aria-label="화재 경보">
  <div role="alert">
   <strong><Icon name="bell"/> {mock?'모의 화재 경보':'화재 경보'}</strong>
   <dl>
    <dt>주차장</dt><dd>{lotName(alert.lot_id)||'주차장 미확인'}</dd>
    <dt>발생 위치</dt><dd>{alert.space_id?(zone?zone+'구역 · ':'')+alert.space_id:zone?zone+'구역 · 칸 미확인':'위치 미확인'}</dd>
    <dt>발생 시각</dt><dd>{time(alert.detected_at)}</dd>
   </dl>
   {Number(alert.level)>=2&&<p>레벨 2 · 대피 안내 · 결제 중단</p>}
  </div>
  <div className="admin-fire-popup-actions">
   <button type="button" className="button danger small" onClick={()=>{onOpen();dismiss()}}>안전 현황 보기</button>
   <button type="button" className="button white small" onClick={dismiss}>닫기</button>
  </div>
 </aside>;
}

function AlertsPanel({data,mode,busy,fire,alertAction,lotName,alertFilters,setAlertFilters}){
 const alerts=useTablePage(data.alertRows||data.alerts);
 const labels={active:t('확인 필요'),acknowledged:t('확인 완료'),resolved:t('해제 완료')};return <><div className="operation-intro"><div><h2>{mode==='admin'?'안전 현황':t('주차장 안전 안내')}</h2></div>{mode==='admin'&&<button className="button danger" disabled={busy} onClick={fire}>LED 경보 시연</button>}</div><Filters value={alertFilters} onChange={filters=>{alerts.setPage(1);setAlertFilters(filters)}} lots={data.lots} statuses={Object.entries(labels)}/><section className="panel"><TablePagination label="안전 현황" pagination={alerts}/>{alerts.rows.map(a=><div className="alert-row" key={a.alert_id}><span className={`stat-icon ${a.status==='resolved'?'green':'orange'}`}><Icon name={a.status==='resolved'?'check':'bell'}/></span><div><h3>{lotName(a.lot_id)} · {a.space_id||t('위치 미확인')}</h3><p>{a.is_mock||a.source==='simulation'||a.source==='manual'?'이벤트 · ':''}{t("화재 ·")} {time(a.detected_at)}</p><strong className={Number(a.level)>=2?'evacuation-label':''}>{Number(a.level)>=2?t('레벨 2 · 대피 안내 · 결제 중단'):t('레벨 1 · 주의')}</strong>{a.acknowledged_at&&<p>{t("확인")} {time(a.acknowledged_at)}</p>}{a.resolved_at&&<p>{t("해제")} {time(a.resolved_at)}</p>}</div><span className={`badge ${a.status==='active'?'orange':'real'}`}>{labels[a.status]}</span>{mode==='admin'&&a.status!=='resolved'&&<button className="button white small" disabled={busy} onClick={()=>alertAction(a.alert_id,a.status==='active'?'acknowledge':'resolve')}>{a.status==='active'?t('확인 처리'):t('경보 해제')}</button>}</div>)}{!alerts.rows.length&&<div className="empty-state"><Icon name="check"/><h3>{t("조회 조건에 맞는 경보가 없습니다")}</h3><p>{t("관리자에서 LED 경보 시연을 실행할 수 있습니다.")}</p></div>}</section></>}
