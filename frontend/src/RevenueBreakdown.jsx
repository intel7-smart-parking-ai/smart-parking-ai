import {money} from './components.jsx';

const zero = {gross_krw:0, canceled_krw:0, net_krw:0, completed_count:0, canceled_count:0};

export function RevenueBars({rows,onSelect,metric='net_krw',scale,format=money,metricLabel='순매출',connect=false}) {
 const max = scale ?? Math.max(1,...rows.map(row=>row[metric]));
 const connectedPoints=rows.map((row,index)=>`${(index+.5)*1000/rows.length},${150-138*Math.max(0,row[metric])/max}`).join(' ');
 return <div className="revenue-plot-scroll"><div className={`revenue-plot${connect?' revenue-plot-connected':''}`} style={{'--bar-count':rows.length}}>
  {rows.map(row=>{
   const detail = row.label+' '+metricLabel+' '+format(row[metric]);
   const bar = <><span className="revenue-bar-track"><span style={{height:100*row[metric]/max+'%'}}/></span><small>{row.label}</small></>;
   return onSelect
    ? <button key={row.key} className="revenue-bar" type="button" title={detail} aria-label={detail} onClick={()=>onSelect(row.key)}>{bar}</button>
    : <div key={row.key} className="revenue-bar" title={detail} aria-label={detail}>{bar}</div>;
  })}
  {connect&&<svg className="revenue-connecting-line" viewBox="0 0 1000 150" preserveAspectRatio="none" aria-hidden="true"><polyline className="revenue-connecting-underlay" points={connectedPoints}/><polyline points={connectedPoints}/>{rows.map((row,index)=><circle key={row.key} cx={(index+.5)*1000/rows.length} cy={150-138*Math.max(0,row[metric])/max} r="4"/>)}</svg>}
 </div></div>;
}

export default function RevenueBreakdown({period,view,report,days,chosen,onChoose}) {
 const hourly = period==='hourly';
 const activeDay = chosen?.date || days.find(day=>day.gross_krw>0)?.date || days[0]?.date;
 const hours = new Map((report.hourly_revenue||[]).map(row=>[row.hour,row]));
 const rows = hourly
  ? Array.from({length:24},(_,index)=>{
     const hour = String(index).padStart(2,'0');
     return {...zero,...hours.get(activeDay+'T'+hour),key:hour,label:hour+'시'};
    })
  : [...report.monthly_revenue].reverse().map(row=>({...row,key:row.month,label:row.month}));
 const title = hourly ? activeDay+' 시간대별 매출' : '월별 매출 추이';
 return <section className="panel revenue-main">
  <div className="panel-header"><h2>{title}</h2>
   {hourly&&<label>조회 날짜 <select aria-label="시간대별 매출 조회 날짜" value={activeDay} onChange={event=>onChoose(event.target.value)}>
    {days.map(day=><option key={day.date} value={day.date}>{day.date}</option>)}
   </select></label>}
   <span className="revenue-hint">결제 시각 기준 · 한국 시간</span>
  </div>
  {hourly&&!Array.isArray(report.hourly_revenue)?<p className="revenue-loading">서버에서 시간대별 매출을 제공하지 않습니다.</p>:
   !rows.length?<p className="revenue-loading">조회 조건에 해당하는 매출이 없습니다.</p>:
   view==='chart'?<RevenueBars rows={rows}/>:<div className="table-scroll revenue-sheet"><table>
    <thead><tr>{[hourly?'시간대':'월','결제 발생액','취소 금액','순매출','완료 거래','취소 거래'].map(label=><th key={label}>{label}</th>)}</tr></thead>
    <tbody>{rows.map(row=><tr key={row.key}><td>{row.label}</td><td>{money(row.gross_krw)}</td><td>{money(row.canceled_krw)}</td><td><b>{money(row.net_krw)}</b></td><td>{row.completed_count}건</td><td>{row.canceled_count}건</td></tr>)}</tbody>
   </table></div>}
 </section>;
}

function stayText(minutes) {
 if (minutes==null) return '기록 없음';
 const total = Math.round(minutes);
 const hours = Math.floor(total/60);
 return hours ? hours+'시간 '+total%60+'분' : total+'분';
}

const dayNames = {weekday:'평일',weekend:'주말',holiday:'공휴일'};
const stayBands = {short:'1시간 미만',medium:'1~3시간',long:'3~24시간',overnight:'24시간 이상'};
const weekdays = ['월','화','수','목','금','토','일'];
const heatHours = Array.from({length:24},(_,hour)=>String(hour).padStart(2,'0'));

function LotHeatmap({name,rows}) {
 const cells = new Map(rows.map(row=>[row.weekday+'-'+row.hour,row.occupancy_pct]));
 return <div className="revenue-heatmap">
  <h4>{name}</h4>
  <div className="revenue-heatmap-scroll"><table aria-label={name+' 요일·시간대별 평균 점유율'}>
   <thead><tr><th scope="col">요일</th>{heatHours.map(hour=><th scope="col" key={hour}>{hour}</th>)}</tr></thead>
   <tbody>{weekdays.map((day,index)=><tr key={day}><th scope="row">{day}</th>{heatHours.map(hour=>{
    const pct=cells.get(index+'-'+hour);
    const label=day+'요일 '+hour+'시 · '+(pct==null?'집계 없음':pct+'%');
    return <td key={hour} title={label} aria-label={label} style={pct==null?undefined:{backgroundColor:'rgba(52,104,207,'+(0.08+pct/116)+')'}}/>;
   })}</tr>)}</tbody>
  </table></div>
 </div>;
}

export function UsageSummary({usage,scope,lots}) {
 if (!usage) return <section className="panel revenue-loading"><h2>이용 현황</h2><p>선택한 월에 집계할 시연 이력이 없습니다.</p></section>;
 const peak = usage.hourly.reduce((best,row)=>row.occupancy_pct>best.occupancy_pct?row:best);
 const measures = [
  ['입차 건수',usage.entries.toLocaleString('ko-KR')+'건'],
  ['하루 평균 입차',(usage.entries/usage.days_covered).toLocaleString('ko-KR',{maximumFractionDigits:1})+'건'],
  ['평균 이용 시간',stayText(usage.avg_stay_minutes)],
  ['평균 점유율',usage.avg_occupancy_pct+'%']
 ];
 return <section className="panel revenue-main">
  <div className="panel-header"><h2>이용 현황 · {scope}</h2><span className="revenue-hint">합성 시연 이력 · {usage.first_day}~{usage.last_day} · 한국 시간</span></div>
  <section className="operation-stats revenue-usage-stats">{measures.map(([label,value])=><article className="panel" key={label}><span>{label}</span><strong>{value}</strong></article>)}</section>
  <div className="panel-header"><h3>시간대별 평균 점유율</h3>{peak.occupancy_pct>0&&<span className="revenue-hint">가장 혼잡한 시간 {peak.hour}시 · {peak.occupancy_pct}%</span>}</div>
  <RevenueBars rows={usage.hourly.map(row=>({...row,key:row.hour,label:row.hour+'시'}))} metric="occupancy_pct" metricLabel="평균 점유율" scale={100} format={value=>value+'%'}/>
  {!!usage.day_type_comparison?.length&&<>
   <div className="panel-header"><h3>평일·주말·공휴일 비교</h3><span className="revenue-hint">공휴일은 평일·주말에서 분리</span></div>
   <div className="table-scroll revenue-sheet"><table>
    <thead><tr>{['구분','집계 일수','하루 평균 입차','하루 평균 순매출','평균 점유율'].map(label=><th key={label}>{label}</th>)}</tr></thead>
    <tbody>{usage.day_type_comparison.map(row=><tr key={row.kind}><th scope="row">{dayNames[row.kind]}</th><td>{row.days}일</td><td>{row.daily_entries}건</td><td>{money(row.daily_net_krw)}</td><td>{row.occupancy_pct}%</td></tr>)}</tbody>
   </table></div>
  </>}
  {usage.stay_distribution&&<>
   <div className="panel-header"><h3>체류 시간 분포</h3><span className="revenue-hint">종료된 주차 세션 기준</span></div>
   <div className="revenue-stay-bands">{Object.entries(stayBands).map(([key,label])=>{
    const count=usage.stay_distribution[key]||0;
    const total=Object.values(usage.stay_distribution).reduce((a,b)=>a+b,0);
    const percent=total?100*count/total:0;
    return <div key={key}><label>{label}<progress max="100" value={percent}/></label><span>{count.toLocaleString('ko-KR')}건 · {percent.toFixed(1)}%</span></div>;
   })}</div>
  </>}
  {usage.heatmaps&&<>
   <div className="panel-header"><h3>주차장별 요일·시간대 혼잡도</h3><span className="revenue-hint">색이 진할수록 평균 점유율이 높습니다.</span></div>
   <div className="revenue-heatmaps">{Object.entries(usage.heatmaps).map(([id,rows])=>
    <LotHeatmap key={id} name={lots.find(lot=>lot.lot_id===id)?.name||id} rows={rows}/>)}</div>
  </>}
  <details className="revenue-usage-table"><summary>일별 입차·점유율 표</summary><div className="table-scroll revenue-sheet"><table>
   <thead><tr><th>날짜</th><th>입차 건수</th><th>평균 점유율</th></tr></thead>
   <tbody>{usage.daily.map(row=><tr key={row.day}><td>{row.day}</td><td>{row.entries.toLocaleString('ko-KR')}건</td><td>{row.occupancy_pct}%</td></tr>)}</tbody>
  </table></div></details>
  <p className="revenue-usage-note">평균 이용 시간은 해당 월에 입차한 종료 세션 기준입니다. 점유율은 전체 주차칸이 제공한 시간 중 차량이 머문 시간의 비율입니다.</p>
 </section>;
}
