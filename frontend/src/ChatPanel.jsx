import {Icon,ChatbotIcon} from './components.jsx';
import {t,getLanguage} from './serviceLocale.js';
import {formatLotName,chatSafetyScope} from './uiState.js';
import {requestId} from './requestId.js';
import {useEffect,useRef,useState,useSyncExternalStore} from 'react';
import {request} from './api.js';
import {createChatSession} from './chatSession.js';
import {mergeSpeechResults} from './speechTranscript.js';
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

export default function ChatPanel({lots,alerts,lotId,voiceInput=false,appAppearance=false,connected=false,onClose}) {
 const spokenDraft = useRef(null);
 const utterance = useRef(null);
 const speechStartTimer = useRef(null);
 const speechPrimed = useRef(false);
 const speechGeneration = useRef(0);
 const [speechError,setSpeechError]=useState('');
 const [voiceReply,setVoiceReply]=useState(null);
 const voiceMuted = useRef(false);
 const [muted,setMuted]=useState(false);
 const playbackError = () => t('음성 답변을 재생할 수 없습니다. 기기의 해당 언어 음성 설정을 확인해 주세요.');
 function stopSpeech() {
  speechGeneration.current++;
  clearTimeout(speechStartTimer.current);
  utterance.current = null;
  window.speechSynthesis?.cancel();
 }
 function isAppleMobile() {
  return /iPhone|iPad|iPod/.test(navigator.userAgent)
   || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
 }
 function primeMobileSpeech() {
  const synth = window.speechSynthesis;
  if (!isAppleMobile() || speechPrimed.current || !synth || !window.SpeechSynthesisUtterance) return;
  // Safari may require the first synthesis request to happen in a tap handler.
  const activation = new window.SpeechSynthesisUtterance('음');
  activation.lang = 'ko-KR';
  activation.volume = 0;
  try {
   synth.speak(activation);
   speechPrimed.current = true;
  } catch {
   // The reply can still be played with the explicit playback button.
  }
 }
 async function playVoiceReply(text, language, manual = false) {
   stopSpeech();
   setSpeechError('');
   const generation=speechGeneration.current;
   const synth=window.speechSynthesis;
   if (!synth || !window.SpeechSynthesisUtterance) {
    setSpeechError(playbackError());
    return;
   }
   let voices=synth.getVoices();
   if (!manual && !voices.length) {
    await new Promise(resolve=>{
     const ready=()=>{clearTimeout(timer);synth.removeEventListener('voiceschanged',ready);resolve()};
     const timer=setTimeout(ready,1500);
     synth.addEventListener('voiceschanged',ready);
    });
    voices=synth.getVoices();
   }
   if(generation!==speechGeneration.current)return;
   const speech = new window.SpeechSynthesisUtterance(formatLotName(text));
   speech.lang = {ko:'ko-KR', en:'en-US', 'zh-CN':'zh-CN', ja:'ja-JP'}[language] || language || 'ko-KR';
   const locale=speech.lang.toLowerCase();
   const voiceLocale = voice => voice.lang.toLowerCase().replaceAll('_', '-');
   const matching=voices.filter(voice=>voiceLocale(voice).split('-')[0]===locale.split('-')[0]);
   let voice=matching.find(voice=>voiceLocale(voice)===locale&&voice.localService)
    ||matching.find(voice=>voiceLocale(voice)===locale)||matching[0];
   if (isAppleMobile() && locale === 'ko-kr') {
    const localVoices = matching.filter(candidate => candidate.localService);
    const voiceLabel = candidate => `${candidate.name} ${candidate.voiceURI}`;
    const yunaVoices = localVoices.filter(candidate => /yuna|유나/i.test(voiceLabel(candidate)));
    const preferredVoices = yunaVoices.length ? yunaVoices : localVoices;
    voice = preferredVoices.find(candidate => /premium|프리미엄/i.test(voiceLabel(candidate)))
     || preferredVoices.find(candidate => /enhanced|향상|고품질/i.test(voiceLabel(candidate)))
     || yunaVoices[0]
     || voice;
    speech.rate = 0.94;
   }
   if (voice) speech.voice = voice;
   speech.onstart = () => {
    if (utterance.current === speech) clearTimeout(speechStartTimer.current);
   };
   speech.onend = () => {
    if (utterance.current !== speech) return;
    clearTimeout(speechStartTimer.current);
    utterance.current = null;
   };
   speech.onerror = () => {
    if(utterance.current!==speech)return;
    clearTimeout(speechStartTimer.current);
    utterance.current=null;
    setSpeechError(playbackError());
   };
   utterance.current = speech;
   speechStartTimer.current = setTimeout(() => {
    if (utterance.current !== speech) return;
    stopSpeech();
    speechPrimed.current = false;
    setSpeechError(playbackError());
   }, 4000);
   try {
    if (synth.paused) synth.resume();
    synth.speak(speech);
   } catch {
    stopSpeech();
    setSpeechError(playbackError());
   }
 }
 const [chat] = useState(() => createChatSession({
  request, makeId: requestId, language: getLanguage(),
  onVoiceReply(text, language) {
   setVoiceReply({text, language});
   if (!voiceMuted.current) void playVoiceReply(text, language);
  },
 }));
 const state = useSyncExternalStore(chat.subscribe,chat.getSnapshot);
 function toggleVoicePlayback() {
  const nextMuted = !voiceMuted.current;
  voiceMuted.current = nextMuted;
  setMuted(nextMuted);
  if (nextMuted) {
   stopSpeech();
   setSpeechError('');
  } else if (voiceReply) {
   void playVoiceReply(voiceReply.text, voiceReply.language, true);
  }
 }
 const {draft,rows,view,busy,error,retry,restartRequired,actionEpoch} = state;
 const [showMap,setShowMap]=useState(false),[listening,setListening]=useState(false);
 const [speaking,setSpeaking]=useState(false);
 const [testMapLot,setTestMapLot]=useState(null);
 const testLots=lots.filter(lot=>testVehicleNumbers[lot.lot_id] && lot.spaces.some(space=>space.status==='occupied' && testVehicleNumbers[lot.lot_id].includes(space.vehicle_number)));
 const testLot=testLots.find(lot=>lot.lot_id===testMapLot);
 const testVehicles=testLot?.spaces.filter(space=>space.status==='occupied' && testVehicleNumbers[testMapLot].includes(space.vehicle_number)) ?? [];
 const voiceTimer=useRef(null);
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
 function stopVoice(){clearTimeout(voiceTimer.current);voiceTimer.current=null;voiceGeneration.current++;recognition.current?.abort();recognition.current=null;setListening(false);setSpeaking(false)}
 useEffect(()=>{const timer=setInterval(()=>chat.poll(),2500);return()=>{clearInterval(timer);chat.cancelPending();stopSpeech();clearTimeout(voiceTimer.current);voiceGeneration.current++;recognition.current?.abort();};},[chat]);
 useEffect(()=>{chat.invalidateAlerts(alerts);},[chat,alerts]);
 useEffect(()=>{if(confirmation||unsafe||busy||restartRequired)stopVoice();},[confirmation,unsafe,busy,restartRequired]);
 useEffect(()=>{if(unsafe)safetyNotice.current?.scrollIntoView({block:'start'});},[unsafe]);
 useEffect(() => {
  if (unsafe) return;
  const questions = transcript.current?.querySelectorAll('.chat-row.mine');
  const latestQuestion = questions?.[questions.length - 1];
  if (!latestQuestion) return;
  latestQuestion.scrollIntoView({block: 'start', behavior: 'instant'});
 }, [rows, unsafe]);
 function edit(value){stopVoice();spokenDraft.current=null;draftRevision.current++;chat.edit(value)}
 function toggleVoice(){
  if(listening||recognition.current){stopVoice();return;}
  const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(!Recognition){setVoiceError(t('이 브라우저는 음성 입력을 지원하지 않아요. 질문을 직접 입력해 주세요.'));return;}
  clearTimeout(voiceTimer.current);
  stopSpeech();
  primeMobileSpeech();
  setVoiceReply(null);
  setSpeechError('');
  spokenDraft.current=null;
  const input=new Recognition(),epoch=++voiceGeneration.current,revision=draftRevision.current;
  let spokenText='';
  const current=()=>epoch===voiceGeneration.current&&revision===draftRevision.current;
  function afterSilence(){
   clearTimeout(voiceTimer.current);
   voiceTimer.current=setTimeout(()=>{
    if(!current())return;
    stopVoice();
    if(!spokenText.trim())return;
    draftRevision.current++;
    setVoiceError('');
    spokenDraft.current=null;
    chat.send({voice:true});
   },2000);
  }
  input.lang={ko:'ko-KR',en:'en-US','zh-CN':'zh-CN',ja:'ja-JP'}[getLanguage()];
  input.continuous=true;
  input.interimResults=true;
  input.onresult=e=>{
   if(!current())return;
   spokenText=mergeSpeechResults(e.results);
   spokenDraft.current=spokenText;
   chat.edit(spokenText);
   afterSilence();
  };
  input.onspeechstart=()=>{
   if(!current())return;
   setSpeaking(true);
   clearTimeout(voiceTimer.current);
   voiceTimer.current=null;
  };
  input.onspeechend=()=>{
   if(!current())return;
   setSpeaking(false);
   afterSilence();
  };
  input.onerror=e=>{
   if(!current())return;
   stopVoice();
   if(e.error!=='aborted'&&e.error!=='no-speech')setVoiceError(e.error==='not-allowed'?t('마이크 접근을 허용해 주세요.'):t('음성을 인식하지 못했어요. 다시 말해 주세요.'));
  };
  input.onend=()=>{
   if(!current())return;
   recognition.current=null;
   setListening(false);
   setSpeaking(false);
   if(!spokenText.trim()){stopVoice();return;}
   // Some browsers end recognition before our silence timer expires.
   if(!voiceTimer.current)afterSilence();
  };
  setVoiceError('');
  try{input.start();recognition.current=input;setListening(true);afterSilence();}catch{stopVoice();setVoiceError(t('음성 입력을 시작하지 못했어요.'));}
 }
 function send(e){
  e.preventDefault();
  if(busy||restartRequired||!draft.trim())return;
  const voice = spokenDraft.current !== null && spokenDraft.current === draft;
  if(listening&&(!voice||confirmation||unsafe))return;
  stopVoice();
  draftRevision.current++;
  setVoiceError('');
  spokenDraft.current = null;
  chat.send({voice});
 }
 const context = view?.vehicle_context;
 const contextState = {
  parked: t('주차 중'),
  pending: t('입차 확인 중'),
  not_found: t('위치 확인 안 됨'),
  unavailable: t('정보 확인 불가'),
  stale: t('정보 갱신 필요'),
 };
 return <section className={`panel chat-panel ${appAppearance ? 'chat-panel--app' : ''}`} aria-label={t("주차 챗봇")}>
  <header className="chat-panel-heading">
   {onClose&&<button type="button" className="mobile-chat-close" aria-label={t('닫기')} onClick={onClose}><Icon name="close"/></button>}
   <button type="button" className="button white" aria-expanded={showMap} onClick={()=>setShowMap(v=>!v)}>{showMap?t('전체 지도 닫기'):t('전체 주차장 지도 미리보기')}</button>
  </header>
  <div className="chat-content">
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
   {!unsafe&&!appAppearance&&<article className="chat-row assistant">
    {appAppearance&&<span className="app-chat-avatar" aria-hidden="true"><ChatbotIcon/></span>}
    <span className="chat-speaker">{t('주차 챗봇')}</span>
    <div className="chat-bubble">{t('안녕하세요. PARK : ON 주차 챗봇입니다.')}<br/>{t('빈자리, 내 차 위치, 주차요금이 궁금하신가요?')}<br/>{t('궁금한 내용을 입력해 주세요.')}</div>
   </article>}
   {appAppearance&&!unsafe&&rows.length===0&&<div className="chat-intro">
    <span><ChatbotIcon/>{t('주차 챗봇')}</span>
    <h1>{t('무엇을 도와드릴까요?')}</h1>
   </div>}
   {!unsafe&&rows.length===0&&<nav className="chat-quick-links" aria-label={t('자주 묻는 질문')}>
    {[
     ['grid','빈자리 확인','주차장별 빈자리 몇 개 있어?'],
     ['car','내 차 찾기','내 차 위치를 찾고 싶어요'],
     ['wallet','주차요금','주차요금 안내해줘'],
     ['pin','전기차 구역','충전 구역 빈자리 알려줘'],
     ['wheelchair','장애인 구역','장애인 구역 빈자리 알려줘'],
     ['info','이용 방법','주차장 이용 방법 알려줘'],
    ].map(([icon,label,question])=><button type="button" key={label} disabled={busy||restartRequired} onClick={()=>{stopVoice();spokenDraft.current=null;draftRevision.current++;chat.edit(t(question));chat.send();}}><Icon name={icon}/><span>{t(label)}</span></button>)}
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
  {speechError&&!unsafe&&<p className="operation-error" role="status">{speechError}</p>}
  {(voiceReply||muted)&&!listening&&!busy&&!unsafe&&!restartRequired&&<button type="button" className="button white" aria-label={t('음성 답변 음소거')} aria-pressed={muted} title={muted?t('음소거 해제'):t('음성 답변 음소거')} onClick={toggleVoicePlayback}>
   <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M11 5 6 9H3v6h3l5 4z"/>
    {muted ? <path d="m16 9 6 6m0-6-6 6"/> : <path d="M15 9a4 4 0 0 1 0 6m3-9a8 8 0 0 1 0 12"/>}
   </svg>
  </button>}
  {retry&&<button type="button" className="button white chat-retry" disabled={busy} onClick={()=>chat.retry()}>{t("같은 요청 다시 시도")}</button>}
  {restartRequired&&<button type="button" className="button white" onClick={()=>{stopVoice();stopSpeech();setVoiceReply(null);setSpeechError('');spokenDraft.current=null;chat.reset();}}>{t("대화 다시 시작")}</button>}
  </div>
  <div className="chat-voice-status chat-voice-status--composer" role="status">
   {listening&&<>
    <span className={`chat-voice-wave${speaking?' is-speaking':''}`} aria-hidden="true"><i/><i/><i/><i/></span>
    {speaking&&<span>{t('말하는 중…')}</span>}
   </>}
  </div>
  <form onSubmit={send} className="search-form chat-form">
   <input aria-label={t("챗봇 질문")} value={draft} onChange={e=>edit(e.target.value)} placeholder={t("메세지를 입력하세요")}/>{voiceInput&&<button type="button" className="button white chat-voice-button" disabled={busy||confirmation||unsafe||restartRequired} aria-label={listening?t('음성 입력 중지'):t('음성 입력')} title={listening?t('음성 입력 중지'):t('음성 입력')} aria-pressed={listening} onClick={toggleVoice}>
   <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{listening?<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>:<>
   <rect x="8" y="2" width="8" height="13" rx="4"/>
   <path d="M4 11v1a8 8 0 0 0 16 0v-1M12 20v3m-4 0h8M8 6h3M8 9h3"/>
   </>}</svg>
   </button>}<button className="button primary chat-send-button" aria-label={busy?t('응답 대기 중…'):t('질문 보내기')} title={busy?t('응답 대기 중…'):t('질문 보내기')} disabled={busy||restartRequired||!draft.trim()||(listening&&(spokenDraft.current!==draft||confirmation||unsafe))}>
   <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{listening?<path d="M12 19V5m-6 6 6-6 6 6"/>:<>
   <path d="m21 3-6.5 18-4-7.5L3 9.5 21 3Z"/>
   <path d="m10.5 13.5 5-5"/>
   </>}</svg>
   </button>
   </form>
  {chargingLot&&!unsafe&&<ChargingStatus language={getLanguage()} initialLot={chargingLot} onClose={()=>setChargingLot(null)}/>}
 </section>;
}
