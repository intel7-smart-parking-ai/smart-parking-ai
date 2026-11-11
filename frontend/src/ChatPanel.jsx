import {Icon,ChatbotIcon} from './components.jsx';
import {createPortal} from 'react-dom';
import {t,getLanguage} from './serviceLocale.js';
import {formatLotName,chatSafetyScope} from './uiState.js';
import {requestId} from './requestId.js';
import {useEffect,useRef,useState,useSyncExternalStore} from 'react';
import {request} from './api.js';
import {createChatSession} from './chatSession.js';
import {chatRecommendations} from './recommendationMapData.js';
import RecommendationMap from './RecommendationMap.jsx';
import ChatResponseCard from './ChatResponseCard.jsx';
import ChargingStatus from './ChargingStatus.jsx';
import ParkingLayoutMap from './ParkingLayoutMap.jsx';
import {nearestMallRestroomSpace} from './mallParkingLayout.js';
import './styles/chat-panel.css';

const testVehicleNumbers = {C: ['1724', '1735'], D: ['7654', '7665']};

function RestroomRecommendation({lot,space,alerts}) {
 const [filter,setFilter]=useState('empty');
 return <section className="chat-answer-map" aria-label="화장실 근처 추천 빈자리">
  <h3>화장실 근처 추천 빈자리 · {space.space_id}</h3>
  <p>빨간색으로 표시했습니다. 시연 도면의 직선거리 기준입니다.</p>
  <ParkingLayoutMap lot={lot} alerts={alerts} filter={filter} setFilter={setFilter}
   recommendedSpaceId={space.space_id} hideTitle hidePreviewControls legendNearMap onSpace={()=>{}}/>
 </section>;
}

const EVACUATION_CHIPS = new Set(['evacuate', 'alert_near', 'alert_where']);

export default function ChatPanel({lots,alerts,lotId,voiceInput=false,appAppearance=false,connected=false}) {
 const [toolbarTarget,setToolbarTarget]=useState(null);
 useEffect(()=>{setToolbarTarget(document.getElementById('mobile-chat-tools'))},[]);
 const [chat] = useState(() => createChatSession({request, makeId:requestId, language:getLanguage()}));
 const state = useSyncExternalStore(chat.subscribe,chat.getSnapshot);
 const {draft,rows,view,busy,error,retry,restartRequired,actionEpoch} = state;
 const [showMap,setShowMap]=useState(false),[listening,setListening]=useState(false);
 const [testMapLot,setTestMapLot]=useState(null);
 const testLots=lots.filter(lot=>testVehicleNumbers[lot.lot_id] && lot.spaces.some(space=>space.status==='occupied' && testVehicleNumbers[lot.lot_id].includes(space.vehicle_number)));
 const testLot=testLots.find(lot=>lot.lot_id===testMapLot);
 const testVehicles=testLot?.spaces.filter(space=>space.status==='occupied' && testVehicleNumbers[testMapLot].includes(space.vehicle_number)) ?? [];
 const recognition=useRef(null),voiceGeneration=useRef(0),draftRevision=useRef(0),transcript=useRef(null),safetyNotice=useRef(null);
 const [voiceError,setVoiceError]=useState('');
 const confirmation = view?.flow==='confirm_action';
 // 대피 화면은 선택한 주차장에 레벨 2 경보가 있을 때만(다른 주차장 경보는 안내 한 줄, 11/11 PM)
 const safetyScope = chatSafetyScope(view,alerts,lotId);
 const unsafe = safetyScope.unsafe;
 const rowSafety = result => Boolean(result?.safety) && safetyScope.relevant;
 const lotLabel = id => formatLotName(lots.find(lot=>lot.lot_id===id)?.name||id);
 const chips = Array.isArray(view?.chips) ? view.chips.filter(chip=>!(safetyScope.otherLotNotice&&EVACUATION_CHIPS.has(chip.id))) : [];
 const [chargingLot,setChargingLot]=useState(null);
 const mallLot=lots.find(lot=>lot.layout_type==='mall');
 useEffect(()=>{if(unsafe)setChargingLot(null);},[unsafe]);
 function stopVoice(){voiceGeneration.current++;recognition.current?.abort();recognition.current=null;setListening(false)}
 useEffect(()=>{const timer=setInterval(()=>chat.poll(),2500);return()=>{clearInterval(timer);chat.cancelPending();voiceGeneration.current++;recognition.current?.abort();};},[chat]);
 useEffect(()=>{chat.invalidateAlerts(alerts);},[chat,alerts]);
 useEffect(()=>{if(confirmation||unsafe)stopVoice();},[confirmation,unsafe]);
 useEffect(()=>{if(unsafe)safetyNotice.current?.scrollIntoView({block:'start'});},[unsafe]);
 useEffect(() => {
  if (unsafe) return;
  const questions = transcript.current?.querySelectorAll('.chat-row.mine');
  const latestQuestion = questions?.[questions.length - 1];
  if (!latestQuestion) return;
  latestQuestion.scrollIntoView({block: 'start', behavior: 'instant'});
 }, [rows, unsafe]);
 function edit(value){draftRevision.current++;chat.edit(value)}
 function toggleVoice(){
  if(recognition.current){recognition.current.stop();return;}
  const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(!Recognition){setVoiceError(t('이 브라우저는 음성 입력을 지원하지 않아요. 질문을 직접 입력해 주세요.'));return;}
  const input=new Recognition(),epoch=++voiceGeneration.current,revision=draftRevision.current;
  input.lang={ko:'ko-KR',en:'en-US','zh-CN':'zh-CN',ja:'ja-JP'}[getLanguage()];
  input.onresult=e=>{if(epoch===voiceGeneration.current&&revision===draftRevision.current)edit(e.results[0][0].transcript);};
  input.onerror=e=>{if(epoch===voiceGeneration.current&&e.error!=='aborted')setVoiceError(e.error==='not-allowed'?t('마이크 접근을 허용해 주세요.'):t('음성을 인식하지 못했어요. 다시 말해 주세요.'));};
  input.onend=()=>{if(epoch===voiceGeneration.current){recognition.current=null;setListening(false);}};
  setVoiceError('');
  try{input.start();recognition.current=input;setListening(true);}catch{setVoiceError(t('음성 입력을 시작하지 못했어요.'));}
 }
 function send(e){e.preventDefault();draftRevision.current++;setVoiceError('');chat.send();}
 const context=view?.vehicle_context;
 const contextState={parked:t('주차 중'),pending:t('입차 확인 중'),not_found:t('위치 확인 안 됨'),unavailable:t('정보 확인 불가'),stale:t('정보 갱신 필요')};
 return <section className={`panel chat-panel ${appAppearance ? 'chat-panel--app' : ''}`} aria-label={t("주차 챗봇")}>
  {toolbarTarget?createPortal(<button className="button white chat-map-preview" aria-expanded={showMap} onClick={()=>setShowMap(v=>!v)}>{showMap?t('전체 지도 닫기'):t('전체 주차장 지도 미리보기')}</button>,toolbarTarget):<header className="chat-panel-heading"><button className="button white" aria-expanded={showMap} onClick={()=>setShowMap(v=>!v)}>{showMap?t('전체 지도 닫기'):t('전체 주차장 지도 미리보기')}</button></header>}
  {appAppearance&&<header className="app-chat-identity"><div><strong>Chat bot</strong><span className={connected?'online':'offline'}><i/>{connected?'Online':'Offline'}</span></div><span className="app-chat-header-icon" aria-hidden="true"><ChatbotIcon/></span></header>}
  {view?.banner?.text&&!safetyScope.otherLotNotice&&<div ref={safetyNotice} className="chat-banner" data-level={view.banner.level} role="alert"><strong>{t("안전 안내")}</strong><span>{formatLotName(view.banner.text)}</span></div>}
  {safetyScope.otherLotNotice&&<div className="chat-banner" data-level="1" role="status"><strong>{t("참고")}</strong><span>{safetyScope.otherLots.map(lotLabel).join(', ')} {t("모의 화재 경보 중")} · {lotLabel(lotId)} {t("정상 운영")}</span></div>}
  {view?.safety&&!safetyScope.otherLotNotice&&<div className="chat-safety" role="alert"><strong>{formatLotName(view.safety.title)||t('안전 안내')}</strong><p>{formatLotName(view.safety.guidance)}</p>{view.safety.vehicle_help&&<small>{formatLotName(view.safety.vehicle_help)}</small>}</div>}
  {context?.car_id&&<div className="chat-context">{t("지금 대화 중:")} <strong>{context.car_id}{t("번")}</strong> · {contextState[context.state]||context.state}</div>}
  {showMap&&!unsafe&&<RecommendationMap/>}
  {!unsafe&&testLots.length>0&&<nav className="chat-chips" aria-label="시험 차량 지도">
   {testLots.map(lot=><button type="button" key={lot.lot_id} aria-expanded={testMapLot===lot.lot_id} onClick={()=>setTestMapLot(current=>current===lot.lot_id?null:lot.lot_id)}>{formatLotName(lot.name)} 시험 차량 지도</button>)}
  </nav>}
  {!unsafe&&testLot&&<section aria-label="시험 차량 정보">
   <p>시연용 번호판 뒤 4자리로 차량을 조회할 수 있습니다.</p>
   <ul>{testVehicles.map(space=><li key={space.space_id}>{space.space_id} · {space.plate_number||space.vehicle_number} (입력 번호 {space.vehicle_number})</li>)}</ul>
   <a href={`/?app=0&lang=${getLanguage()}&lot=${testMapLot}&screen=map&page=fee`}>요금 조회 · 모의 결제 열기</a>
   <ChatResponseCard card={{type:'parking_map',data:{lot_ids:[testMapLot],route:{lot_id:testMapLot,alternatives:testVehicles.map(space=>space.space_id)}}}} lots={lots} alerts={alerts} allowMap/>
  </section>}
  <div ref={transcript} className="chat-transcript" role="log" aria-live="polite">
   {!unsafe&&<article className="chat-row assistant">
    {appAppearance&&<span className="app-chat-avatar" aria-hidden="true"><ChatbotIcon/></span>}
    <span className="chat-speaker">{t('주차 챗봇')}</span>
    <div className="chat-bubble">{t('안녕하세요. PARK : ON 주차 챗봇입니다.')}<br/>{t('빈자리, 내 차 위치, 주차요금이 궁금하신가요?')}<br/>{t('궁금한 내용을 입력해 주세요.')}</div>
   </article>}
   {!unsafe&&rows.length===0&&<nav className="chat-quick-links" aria-label={t('자주 묻는 질문')}>
    {[
     ['grid','빈자리 확인','주차장별 빈자리 몇 개 있어?'],
     ['car','내 차 찾기','내 차 위치를 찾고 싶어요'],
     ['wallet','주차요금','주차요금 안내해줘'],
     ['pin','전기차 구역','충전 구역 빈자리 알려줘'],
     ['wheelchair','장애인 구역','장애인 구역 빈자리 알려줘'],
     ['info','이용 방법','주차장 이용 방법 알려줘'],
    ].map(([icon,label,question])=><button type="button" key={label} disabled={busy||restartRequired} onClick={()=>{stopVoice();draftRevision.current++;chat.edit(t(question));chat.send();}}><Icon name={icon}/><span>{t(label)}</span></button>)}
   </nav>}
   {rows.map((row,i)=>{
    const question=rows[i-1];
    const recommendedSpace=connected&&mallLot&&!unsafe&&row.result&&!rowSafety(row.result)&&question?.who==='user'
     ?nearestMallRestroomSpace(mallLot,question.text,alerts):null;
    return <article className={`chat-row ${row.who==='user'?'mine':'assistant'}`} key={i}>
   {appAppearance&&row.who!=='user'&&<span className="app-chat-avatar" aria-hidden="true"><ChatbotIcon/></span>}
   <span className="chat-speaker">{row.who==='user'?t('나'):t('주차 챗봇')}</span>
   {!(row.result?.card?.data?.payment_summary && !unsafe && !rowSafety(row.result))&&<div className="chat-bubble">{formatLotName(row.text)}</div>}
   {row.result?.card&&<ChatResponseCard card={row.result.card} language={row.result.language} lots={lots} alerts={alerts} allowMap={!unsafe&&!rowSafety(row.result)} onOpenCharging={setChargingLot}/>}
   {recommendedSpace&&<RestroomRecommendation lot={mallLot} space={recommendedSpace} alerts={alerts}/>}
   {!unsafe&&chatRecommendations(row.result).length>0&&<RecommendationMap recommended={chatRecommendations(row.result)}/>}
  </article>})}
   {busy&&<div className="chat-row assistant chat-waiting" role="status" aria-label={t('응답 대기 중…')}>
    {appAppearance&&<span className="app-chat-avatar" aria-hidden="true"><ChatbotIcon/></span>}
    <div className="chat-bubble chat-waiting-dots" aria-hidden="true"><i/><i/><i/></div>
   </div>}
   </div>
  {confirmation&&!unsafe&&<p className="chat-confirm-note">{t("내용을 확인한 뒤 아래 버튼으로 진행해 주세요.")}</p>}
  {chips.length>0&&<div className="chat-chips" aria-label={t("추천 선택지")}>{chips.map((chip,j)=><button type="button" key={`${actionEpoch}-${chip.id}-${j}`} disabled={busy||restartRequired||(unsafe&&(Boolean(chip.confirmation_token)||/^confirm_/.test(chip.id)))} onClick={()=>{stopVoice();chat.choose(chip,actionEpoch);}}>{formatLotName(chip.label)||chip.id}</button>)}</div>}
  {(error||voiceError)&&<p className="operation-error" role="status">{error||voiceError}</p>}
  {retry&&<button type="button" className="button white chat-retry" disabled={busy} onClick={()=>chat.retry()}>{t("같은 요청 다시 시도")}</button>}
  {restartRequired&&<button type="button" className="button white" onClick={()=>{stopVoice();chat.reset();}}>{t("대화 다시 시작")}</button>}
  <form onSubmit={send} className="search-form chat-form"><input aria-label={t("챗봇 질문")} value={draft} onChange={e=>edit(e.target.value)} placeholder={t("메세지를 입력하세요")}/>{voiceInput&&<button type="button" className="button white chat-voice-button" disabled={busy||confirmation||unsafe||restartRequired} aria-label={listening?t('듣기 종료'):t('음성 입력')} title={listening?t('듣기 종료'):t('음성 입력')} aria-pressed={listening} onClick={toggleVoice}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="8" y="2" width="8" height="13" rx="4"/><path d="M4 11v1a8 8 0 0 0 16 0v-1M12 20v3m-4 0h8M8 6h3M8 9h3"/></svg></button>}<button className="button primary chat-send-button" aria-label={busy?t('응답 대기 중…'):t('질문 보내기')} title={busy?t('응답 대기 중…'):t('질문 보내기')} disabled={busy||listening||restartRequired||!draft.trim()}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m21 3-6.5 18-4-7.5L3 9.5 21 3Z"/><path d="m10.5 13.5 5-5"/></svg></button></form>
  {chargingLot&&!unsafe&&<ChargingStatus language={getLanguage()} initialLot={chargingLot} onClose={()=>setChargingLot(null)}/>}
 </section>;
}
