import {requestId} from './requestId.js';
import {useRef,useState} from 'react';
import {request} from './api.js';
import {chatRecommendations} from './recommendationMapData.js';
import RecommendationMap from './RecommendationMap.jsx';
import ChatResponseCard from './ChatResponseCard.jsx';
import './styles/chat-panel.css';

function replyText(result){
 if(typeof result?.text==='string'&&result.text.trim())return result.text;
 return (Array.isArray(result?.messages)?result.messages:[]).map(item=>typeof item==='string'?item:item?.text).filter(Boolean).join('\n');
}

export default function ChatPanel({lots,alerts}){
 const session=useRef(requestId());
 const [text,setText]=useState(''),[rows,setRows]=useState([]),[busy,setBusy]=useState(false),[error,setError]=useState(''),[showMap,setShowMap]=useState(false);
 const latest=rows.at(-1)?.result;
 async function submit(body,userLabel){
  if(busy)return;
  setBusy(true);setError('');
  try{
   const result=await request('/chat',{method:'POST',body:{session_id:session.current,...body,request_id:requestId()}},'team');
   const answer=replyText(result);
   if(!answer)throw new Error('챗봇 응답 형식을 확인해주세요.');
   setRows(old=>[...old,{who:'나',text:userLabel},{who:'주차 안내',text:answer,result,recommendations:chatRecommendations(result)}]);
   setText('');
  }catch(err){setError(err.status===404?'챗봇 API 연결 대기 중입니다. 담당 서버에 /api/v1/chat 라우터 연결이 필요합니다.':err.status===409?'응답 처리 중입니다. 잠시 후 다시 시도해 주세요.':err.status===400?'질문을 다시 입력해 주세요.':err.status===503?'지금은 정보를 가져올 수 없어요.':err.message)}finally{setBusy(false)}
 }
 function send(e){e.preventDefault();const input=text.trim();if(input)submit({text:input},input)}
 function choose(chip){if(typeof chip?.id!=='string'||!chip.id)return;submit({chip_id:chip.id,...(chip.confirmation_token?{confirmation_token:chip.confirmation_token}:{})},chip.label||'선택')}
 const context=latest?.vehicle_context;
 const contextState={parked:'주차 중',pending:'입차 확인 중',not_found:'위치 확인 안 됨',unavailable:'정보 확인 불가',stale:'정보 갱신 필요'};
 return <section className="panel chat-panel">
  <header className="chat-panel-heading"><div><h2>주차 안내 챗봇</h2><p>빈자리와 차량 위치를 확인하고, 안내된 버튼으로 다음 단계를 진행하세요.</p></div><button className="button white" aria-expanded={showMap} onClick={()=>setShowMap(v=>!v)}>{showMap?'전체 지도 닫기':'전체 주차장 지도 미리보기'}</button></header>
  {latest?.banner?.text&&<div className="chat-banner" role="alert"><strong>모의 경보 안내</strong><span>{latest.banner.text}</span></div>}
  {context?.car_id&&<div className="chat-context">지금 대화 중: <strong>{context.car_id}번</strong> · {contextState[context.state]||context.state}</div>}
  {showMap&&<RecommendationMap/>}
  <div className="chat-transcript" role="log" aria-live="polite">{rows.map((row,i)=><article className={`chat-row ${row.who==='나'?'mine':'assistant'}`} key={i}>
   <span className="chat-speaker">{row.who}</span>
   {row.result?.safety&&<div className="chat-safety" role="alert"><strong>{row.result.safety.title||'사람 먼저 · 모의 화재 안내'}</strong><p>{row.result.safety.guidance}</p>{row.result.safety.vehicle_help&&<small>{row.result.safety.vehicle_help}</small>}</div>}
   <div className="chat-bubble">{row.text}</div>
   {row.result?.card&&<ChatResponseCard card={row.result.card} lots={lots} alerts={alerts} allowMap={!latest?.safety&&!row.result.safety}/>}
   {row.recommendations?.length>0&&<RecommendationMap recommended={row.recommendations}/>}
   {row.result?.flow==='confirm_action'&&<p className="chat-confirm-note">모의 결제 확인은 아래 버튼으로 진행해 주세요.</p>}
   {Array.isArray(row.result?.chips)&&row.result.chips.length>0&&<div className="chat-chips" aria-label="추천 선택지">{row.result.chips.map((chip,j)=><button type="button" key={`${chip.id}-${j}`} disabled={busy||i!==rows.length-1} onClick={()=>choose(chip)}>{chip.label||chip.id}</button>)}</div>}
  </article>)}</div>
  {error&&<p className="operation-error" role="status">{error}</p>}
  <form onSubmit={send} className="search-form chat-form"><input aria-label="챗봇 질문" maxLength={200} value={text} onChange={e=>setText(e.target.value)} placeholder="빈자리나 차량 위치를 질문하세요"/><button className="button primary" disabled={busy||!text.trim()}>{busy?'응답 대기 중…':'질문 보내기'}</button></form>
 </section>
}
