import {useEffect,useState} from 'react';
import ParkingLayoutMap from './ParkingLayoutMap.jsx';
import {loadLots} from './api.js';
import {airportZone} from './airportZones.js';
import './styles/presentation-demo.css';

const scenes = {
 C: [
  [25,'공항 주차장 둘러보기','P1 국내선, P2 국제선, P3 여객·화물 주차장과 순환 셔틀을 확인합니다.'],
  [40,'3일 장기주차 예약','출국 전 P3 장기주차를 선택합니다. 짧은 방문이라면 P1 단기 예약을 이용할 수 있습니다.','8150 · P3 장기 예약 접수 · 3일'],
  [40,'항공편 집중 시간대 혼잡 안내','도착할 때 빈자리가 남을 구역을 골라 안내합니다.','30분 후 예상 빈자리 · P1 2면 · P2 6면 · P3 8면 → P3 추천'],
  [25,'귀국 후 내 차 찾기','차량번호 뒤 네 자리로 장기주차 위치를 다시 확인합니다.','8150 → P3 B-29','B-29'],
  [20,'관리자 안전 관제','화물청사 뒤편 사각지대 정차 신호를 확인하고 현장 확인을 요청합니다.','모의 경보 · 사각지대 정차 → 관리자 확인'],
 ],
 D: [
  [25,'영화 관람 후 내 차 찾기','차량번호 뒤 네 자리로 몰 주차구역의 차량을 찾습니다.','4268 → 몰 C-09','C-09'],
  [30,'몰과 백화점 보행 동선','지도에서 몰 연결 출입구와 에스컬레이터, C구역을 확인합니다.','몰 연결 출입구 → C구역','C-09'],
  [40,'출차 전 주차요금 정산','주차요금을 확인하고 출차 전에 모의 결제를 마칩니다.','4268 · 모의 결제 완료 · 시연 요금 4,500원','C-09'],
  [25,'전기차 전용구역 관제','같은 전기차 전용면에서 일반 차량과 등록 전기차의 표시를 비교합니다.','D-05 일반 차량 4270 · 위반 의심 / D-06 전기차 9001 · 정상','D-05'],
 ],
};

export default function PresentationDemo({lotId}) {
 const [index,setIndex]=useState(0);
 const [playing,setPlaying]=useState(true);
 const [lot,setLot]=useState(null);
 const [error,setError]=useState('');
 const [filter,setFilter]=useState('all');
 const [detail,setDetail]=useState(null);
 const sequence=scenes[lotId];
 const [seconds,title,narration,result,spaceId]=sequence[index];
 useEffect(()=>{
  let active=true;
  loadLots(undefined,{keepSimulationActive:false}).then(lots=>{
   if (!active) return;
   const selected=lots.find(item=>item.lot_id===lotId);
   if(selected) setLot(selected);
   else setError('주차장 도면을 찾지 못했습니다.');
  }).catch(()=>{if(active) setError('주차장 도면을 불러오지 못했습니다. 서버 연결을 확인해 주세요.');});
  return ()=>{active=false};
 },[lotId]);
 useEffect(()=>{
  if (!playing || !lot) return;
  const timer=setTimeout(()=>{
   if(index===sequence.length-1) setPlaying(false);
   else setIndex(index+1);
  },seconds*1000);
  return ()=>clearTimeout(timer);
 },[playing,lot,index,seconds,sequence.length]);

 const displayLot=lot&&{...lot,spaces:lot.spaces.map((space,position)=>{
  const occupied=lotId==='D'&&index===3
   ? {'D-05':['4270','general'],'D-06':['9001','ev']}[space.space_id]
   : spaceId===space.space_id ? [lotId==='C'?'8150':'4268','general'] : null;
  const background=lotId==='C'&&index===2&&position%4!==0;
  return {...space,session_id:null,car_id:null,plate_number:occupied?.[0]??null,vehicle_number:occupied?.[0]??null,vehicle_type:occupied?.[1]??null,status:occupied||background?'occupied':'empty'};
 })};
 const forecast=displayLot&&lotId==='C'&&index===2
  ? ['A','B','C'].map((zone,i)=>{
   const empty=displayLot.spaces.filter(space=>airportZone(space)===zone&&space.status==='empty').length;
   return ['P1','P2','P3'][i]+' '+empty+'면';
  }).join(' · ') : '';
 function move(next){setDetail(null);setFilter('all');setIndex(next);}
 return <main className="presentation-demo">
  <header><a href="/">PARK : ON</a><span>발표용 가상 시뮬레이션</span></header>
  <section className="presentation-stage" aria-live="polite">
   <p>{lot?.name||(lotId==='C'?'김해공항 주차장':'신세계 백화점 주차장')} · {index+1} / {sequence.length} · {seconds}초</p>
   <h1>{title}</h1><p>{narration}</p>{result&&<strong>{result}</strong>}{forecast&&<p>도면의 현재 빈자리 · {forecast}</p>}
  </section>
  <nav aria-label="시연 재생 제어">
   <button type="button" disabled={index===0} onClick={()=>move(index-1)}>이전 장면</button>
   <button type="button" onClick={()=>setPlaying(!playing)}>{playing?'일시정지':'재생'}</button>
   <button type="button" disabled={index===sequence.length-1} onClick={()=>move(index+1)}>다음 장면</button>
   <button type="button" onClick={()=>{move(0);setPlaying(true)}}>처음부터</button>
  </nav>
  {error&&<p role="alert">{error}</p>}
  {displayLot&&<ParkingLayoutMap key={index} lot={displayLot} filter={filter} setFilter={setFilter} alerts={[]} found={spaceId?{lot_id:lotId,space_id:spaceId}:null} onSpace={setDetail} hidePreviewControls admin={lotId==='D'&&index===3}/>}
  {detail&&<p role="status">{detail.space_id} · {detail.vehicle_number||'차량번호 없음'}</p>}
 </main>;
}
