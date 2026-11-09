import {ChatbotIcon} from './components.jsx';
import {createPortal} from 'react-dom';
import {t,getLanguage} from './serviceLocale.js';
import {formatLotName} from './uiState.js';
import {requestId} from './requestId.js';
import {useEffect,useRef,useState,useSyncExternalStore} from 'react';
import {request} from './api.js';
import {createChatSession} from './chatSession.js';
import {chatRecommendations} from './recommendationMapData.js';
import RecommendationMap from './RecommendationMap.jsx';
import ChatResponseCard from './ChatResponseCard.jsx';
import './styles/chat-panel.css';

export default function ChatPanel({lots,alerts,voiceInput=false,appAppearance=false,connected=false}) {
 const [toolbarTarget,setToolbarTarget]=useState(null);
 useEffect(()=>{setToolbarTarget(document.getElementById('mobile-chat-tools'))},[]);
 const [chat] = useState(() => createChatSession({request, makeId:requestId, language:getLanguage()}));
 const state = useSyncExternalStore(chat.subscribe,chat.getSnapshot);
 const {draft,rows,view,busy,error,retry,restartRequired,actionEpoch} = state;
 const [showMap,setShowMap]=useState(false),[listening,setListening]=useState(false);
 const recognition=useRef(null),voiceGeneration=useRef(0),draftRevision=useRef(0),transcript=useRef(null),safetyNotice=useRef(null);
 const [voiceError,setVoiceError]=useState('');
 const confirmation = view?.flow==='confirm_action';
 const unsafe = Boolean(view?.safety)||Number(view?.banner?.level)>=2;
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
 return <section className="panel chat-panel" aria-label={t("주차 챗봇")}>
  {toolbarTarget?createPortal(<button className="button white chat-map-preview" aria-expanded={showMap} onClick={()=>setShowMap(v=>!v)}>{showMap?t('전체 지도 닫기'):t('전체 주차장 지도 미리보기')}</button>,toolbarTarget):<header className="chat-panel-heading"><button className="button white" aria-expanded={showMap} onClick={()=>setShowMap(v=>!v)}>{showMap?t('전체 지도 닫기'):t('전체 주차장 지도 미리보기')}</button></header>}
  {appAppearance&&<header className="app-chat-identity"><div><strong>Chat bot</strong><span className={connected?'online':'offline'}><i/>{connected?'Online':'Offline'}</span></div><span className="app-chat-header-icon" aria-hidden="true"><ChatbotIcon/></span></header>}
  {view?.banner?.text&&<div ref={safetyNotice} className="chat-banner" data-level={view.banner.level} role="alert"><strong>{t("안전 안내")}</strong><span>{formatLotName(view.banner.text)}</span></div>}
  {view?.safety&&<div className="chat-safety" role="alert"><strong>{formatLotName(view.safety.title)||t('안전 안내')}</strong><p>{formatLotName(view.safety.guidance)}</p>{view.safety.vehicle_help&&<small>{formatLotName(view.safety.vehicle_help)}</small>}</div>}
  {context?.car_id&&<div className="chat-context">{t("지금 대화 중:")} <strong>{context.car_id}{t("번")}</strong> · {contextState[context.state]||context.state}</div>}
  {showMap&&!unsafe&&<RecommendationMap/>}
  <div ref={transcript} className="chat-transcript" role="log" aria-live="polite">{rows.map((row,i)=><article className={`chat-row ${row.who==='user'?'mine':'assistant'}`} key={i}>
   {appAppearance&&row.who!=='user'&&<span className="app-chat-avatar" aria-hidden="true"><ChatbotIcon/></span>}
   <span className="chat-speaker">{row.who==='user'?t('나'):t('주차 챗봇')}</span>
   <div className="chat-bubble">{formatLotName(row.text)}</div>
   {row.result?.card&&<ChatResponseCard card={row.result.card} language={row.result.language||getLanguage()} lots={lots} alerts={alerts} allowMap={!unsafe&&!row.result.safety}/>}
   {!unsafe&&chatRecommendations(row.result).length>0&&<RecommendationMap recommended={chatRecommendations(row.result)}/>}
  </article>)}</div>
  {confirmation&&!unsafe&&<p className="chat-confirm-note">{t("내용을 확인한 뒤 아래 버튼으로 진행해 주세요.")}</p>}
  {Array.isArray(view?.chips)&&view.chips.length>0&&<div className="chat-chips" aria-label={t("추천 선택지")}>{view.chips.map((chip,j)=><button type="button" key={`${actionEpoch}-${chip.id}-${j}`} disabled={busy||restartRequired||(unsafe&&(Boolean(chip.confirmation_token)||/^confirm_/.test(chip.id)))} onClick={()=>{stopVoice();chat.choose(chip,actionEpoch);}}>{formatLotName(chip.label)||chip.id}</button>)}</div>}
  {(error||voiceError)&&<p className="operation-error" role="status">{error||voiceError}</p>}
  {retry&&<button type="button" className="button white chat-retry" disabled={busy} onClick={()=>chat.retry()}>{t("같은 요청 다시 시도")}</button>}
  {restartRequired&&<button type="button" className="button white" onClick={()=>{stopVoice();chat.reset();}}>{t("대화 다시 시작")}</button>}
  <form onSubmit={send} className="search-form chat-form"><input aria-label={t("챗봇 질문")} value={draft} onChange={e=>edit(e.target.value)} placeholder={t("메세지를 입력하세요")}/>{voiceInput&&<button type="button" className="button white chat-voice-button" disabled={busy||confirmation||unsafe||restartRequired} aria-label={listening?t('듣기 종료'):t('음성 입력')} title={listening?t('듣기 종료'):t('음성 입력')} aria-pressed={listening} onClick={toggleVoice}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="8" y="2" width="8" height="13" rx="4"/><path d="M4 11v1a8 8 0 0 0 16 0v-1M12 20v3m-4 0h8M8 6h3M8 9h3"/></svg></button>}<button className="button primary chat-send-button" aria-label={busy?t('응답 대기 중…'):t('질문 보내기')} title={busy?t('응답 대기 중…'):t('질문 보내기')} disabled={busy||listening||restartRequired||!draft.trim()}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m21 3-6.5 18-4-7.5L3 9.5 21 3Z"/><path d="m10.5 13.5 5-5"/></svg></button></form>
 </section>;
}
