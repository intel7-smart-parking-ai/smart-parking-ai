import {useState} from 'react';

export default function CongestionForecast({data}){
 const [open,setOpen]=useState(false);
 const date=new Date(data.target).toLocaleDateString('ko-KR',{timeZone:'Asia/Seoul',month:'long',day:'numeric'});
 return <div className="chat-forecast">
  <button type="button" className="button primary" aria-expanded={open} onClick={()=>setOpen(!open)}>{open?'시간대별 혼잡도 접기':'시간대별 혼잡도 보기'}</button>
  {open&&<section className="chat-data-card" aria-label="시간대별 시연 혼잡 예측">
   <h3>{data.lot_id==='C'?'김해공항':'신세계'} · {date}</h3>
   <p className="chat-forecast-note">시뮬레이션 기반 예측 · 실제 잔여석을 보장하지 않아요.</p>
   <div className="chat-forecast-hours">{data.hours?.map(item=>{
    const hour=new Date(item.at).toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false});
    return <div className="chat-forecast-hour" key={item.at} data-selected={new Date(item.at).getHours()===new Date(data.target).getHours()}>
     <span>{hour}</span><meter min="0" max={item.capacity} value={item.capacity-item.empty} aria-label={`${hour} 예상 점유 ${item.capacity-item.empty}칸`}/><strong>{item.level}</strong><span>약 {item.empty}자리</span>
    </div>;
   })}</div>
   <small>시연 기록 검증 평균 오차: 약 {data.mae_spaces}자리 · {data.validation_samples}개 시간대 평가</small>
  </section>}
 </div>;
}
