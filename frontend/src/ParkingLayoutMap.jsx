import {t} from './serviceLocale.js';
import {useEffect,useRef,useState} from 'react';
import {reservedParkingStatus} from './uiState.js';
import {request} from './api.js';
import {Icon, ParkingMap as FallbackMap, statuses, types} from './components.jsx';
import './styles/layout-maps.css';
import MallParkingMap from './MallParkingMap.jsx';
import {airportZone, airportZones} from './airportZones.js';
import {bayGeometry} from './bayGeometry.js';

const names = {basic: t('기본형'), angled: t('사선형'), loop: t('일방순환형'), airport: t('김해공항 주차장 · 터미널 배치도'), l_shape: t('L자복합형'), mall: t('신세계 백화점 주차장 B1 · 대형 주차장')};
function Arrow({x, y, rotate = 0}) {
  return <g className="pdf-road-arrow" transform={`translate(${x} ${y}) rotate(${rotate})`} aria-hidden="true"><path d="M0 28V-23M-12-9 0-23 12-9"/></g>;
}
function Zone({x, y, zone}) {
  return <g className={`pdf-zone zone-${zone}`} transform={`translate(${x} ${y})`} aria-hidden="true"><rect x="-65" y="-17" width="130" height="34" rx="12"/><text textAnchor="middle" y="6">{zone}{t("구역")}</text></g>;
}
function Gate({x, y, label}) {
  return <g className="pdf-gate" transform={`translate(${x} ${y})`} aria-hidden="true"><rect x="-42" y="-18" width="84" height="36" rx="6"/><text textAnchor="middle" y="6">{label}</text></g>;
}
const emergencyExitPositions = {
  basic: [145, 965],
  angled: [145, 80],
  airport: [140, 300],
  mall: [768, 979],
};

function EmergencyExit({kind}) {
  const position = emergencyExitPositions[kind];
  if (!position) return null;
  return <g className="pdf-emergency-exit" transform={'translate(' + position.join(' ') + ')'} role="img" aria-label={t("비상구 예시 위치")}>
    <title>{t("비상구 예시 위치")}</title>
    <image href="/icons/emergency-exit.png" x="-21" y="-21" width="42" height="42"/>
  </g>;
}

function MiniCar({x, y, number, rotation = 0}) {
  const label = String(number ?? '');
  const badgeWidth = Math.max(64, label.length * 20 + 16);
  return <g transform={`translate(${x} ${y})`} aria-hidden="true"><g transform={`rotate(${rotation}) scale(1.2)`}><g className="pdf-car-wheels"><rect x="-30" y="-19" width="15" height="9" rx="3"/><rect x="16" y="-19" width="15" height="9" rx="3"/><rect x="-30" y="10" width="15" height="9" rx="3"/><rect x="16" y="10" width="15" height="9" rx="3"/></g><rect className="pdf-car-body" x="-42" y="-15" width="84" height="30" rx="12"/><rect className="pdf-car-window" x="-23" y="-12" width="46" height="24" rx="5"/></g><rect className="pdf-car-plate" x={-badgeWidth / 2} y="-22" width={badgeWidth} height="44" rx="8"/><text className="pdf-car-number" style={{fontSize:label.length > 2 ? 28 : 34}} textAnchor="middle" dominantBaseline="central" y="0">{label}</text></g>;
}
function Bay({space, points, x, y, vertical, labelY, carY, carX, carRotation, stopPath, filter, alerts, found, lot, onSpace, fees, admin}) {
  const parkingStatus = admin ? reservedParkingStatus(space) : null;
  const geometry = bayGeometry(points);
  const dim = filter !== 'all' && !(filter === 'empty' ? space.status === 'empty' : filter.startsWith('source:')?space.source===filter.slice(7):space.space_type === filter);
  const warned = alerts.some(a => a.lot_id === lot.lot_id && (!a.space_id || a.space_id === space.space_id) && a.status !== 'resolved');
  const selected = found?.lot_id === lot.lot_id && found?.space_id === space.space_id;
  const number = lot.type === 'real' ? space.car_id : space.vehicle_number;
  const vertices=points.trim().split(/\s+/).map(point=>point.split(',').map(Number));
  const width=Math.max(...vertices.map(point=>point[0]))-Math.min(...vertices.map(point=>point[0]));
  const compact=width<150;
  const cardWidth=compact?Math.min(66,width-9):Math.min(220,width-45);
  const cardTop=geometry.labelY+(compact?16:18);
  const plateSize=compact?10:22;

  const label = `${space.space_id} ${types[space.space_type]} ${statuses[space.status]}${admin&&(space.car_id||number) ? ' 차량 ' + (lot.type === 'real' ? space.car_id : space.plate_number || number) : ''}`;
  return <g className={`pdf-bay ${space.status} ${space.space_type} ${dim ? 'dim' : ''} ${selected ? 'found' : ''} ${warned ? 'warned' : ''}`} role="button" tabIndex={0} aria-label={parkingStatus ? label+' · '+parkingStatus : label} onClick={() => onSpace(space)} onKeyDown={e => {if (e.key === 'Enter' || e.key === ' ') {e.preventDefault(); onSpace(space);}}}>
    <title>{label}{parkingStatus && ' · '+parkingStatus}</title><polygon className="pdf-bay-shape" points={points}/>
    {parkingStatus && <g className="reserved-parking-marker" transform={`translate(${geometry.centerX} ${geometry.centerY+28})`}><circle r="12"/><text textAnchor="middle" y="6">{parkingStatus===t('확인 필요')?'?':'!'}</text></g>}
    {(admin || space.status !== 'occupied') && ['ev','accessible'].includes(space.space_type) && <g className="bay-floor-symbol" opacity={space.status==='occupied'?.12:.95} transform={`translate(${geometry.centerX} ${geometry.centerY+8}) skewX(${geometry.floorSymbolSkew}) scale(${lot.lot_id==='C' && space.space_type==='ev' ? .42 : geometry.floorSymbolScale})`} aria-hidden="true">
      {space.space_type==='accessible'?<g fill="none" stroke="#fff" strokeWidth="8" strokeLinecap="round" strokeLinejoin="round"><circle cx="-4" cy="-30" r="9" fill="#fff" stroke="none"/><path d="M-8-12V15H18L32 39M-7-1H17M-20 5A29 29 0 1 0 14 32"/></g>:<g fill="none" stroke="#fff" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round"><path d="M-40 10V-5L-28-22H16L29-5H36V13H-40M-27-22-32-5H22L16-22"/><circle cx="-25" cy="13" r="9" fill="#589b72"/><circle cx="21" cy="13" r="9" fill="#589b72"/><path d="M36-4H43V-19H48M44-24V-16M51-24V-16"/><text x="0" y="44" textAnchor="middle" fill="#fff" stroke="none" fontSize="18" fontWeight="700">{t("전기차 전용")}</text></g>}
    </g>}
    {stopPath && <path className="pdf-wheel-stop" d={vertical ? geometry.stopPath : stopPath} aria-hidden="true"/>}
    <text className="pdf-bay-label" x={geometry.labelX} y={geometry.labelY} style={{fontSize:geometry.labelSize}} dominantBaseline="middle" textAnchor="middle">{space.space_id}</text>
    {admin && space.status === 'occupied' && <g className="bay-vehicle-card" transform={`translate(${geometry.centerX} ${cardTop})`}>
      <rect x={-cardWidth/2} y="0" width={cardWidth} height={compact?27:33} rx={compact?5:7} fill="#fff" stroke="#b5c5df" strokeWidth="1.5"/>
      <text x="0" y={compact?17:23} textAnchor="middle" fontSize={plateSize} fontWeight="750" fill="#1e3352">{space.plate_number||space.vehicle_number||(space.car_id?t('스티커 ')+space.car_id:t('번호 미제공'))}</text>
      <rect className="bay-fee-border" x={-cardWidth/2} y={compact?32:40} width={cardWidth} height={compact?20:29} rx={compact?5:7} fill="#fff" stroke="#b5c5df" strokeWidth="1.5"/>
      <text x="0" y={compact?46:61} textAnchor="middle" fontSize={compact?8.5:20} fontWeight="700">
        <tspan fill="#26384c">{fees?.[space.session_id]?.duration_minutes!=null ? fees[space.session_id].duration_minutes+t('분') : space.entry_at ? Math.max(0,Math.floor((Date.now()-Date.parse(space.entry_at))/60000))+t('분') : t('시간 미제공')}</tspan>
        <tspan fill="#929baa"> · </tspan>
        <tspan fill="#c93636" fontWeight="750">{Number.isFinite(fees?.[space.session_id]?.amount_krw)?fees[space.session_id].amount_krw.toLocaleString('ko-KR')+t('원'):t('요금 미확인')}</tspan>
      </text>
    </g>}
    {!admin && space.status === 'occupied' && <g className="user-bay-status" aria-hidden="true"><rect x={geometry.centerX-(compact?29:60)} y={geometry.centerY-(compact?4:10)} width={compact?58:120} height={compact?24:44} rx={compact?4:6} fill="#fff" fillOpacity=".97"/><text x={geometry.centerX} y={geometry.centerY+(compact?12:20)} textAnchor="middle" fontSize={compact?14:26} fontWeight="800" fill="#173861">{t("주차 중")}</text></g>}
    {warned && <text className="pdf-warning" x={geometry.centerX} y={geometry.labelY} dx={50} fill="#bd2929" fontSize={22} textAnchor="middle">⚠</text>}
  </g>;
}
const rectangle = (x, y, w, h) => `${x},${y} ${x+w},${y} ${x+w},${y+h} ${x},${y+h}`;

export default function ParkingLayoutMap(props) {
  const {lot, filter, setFilter} = props;
  const activeAlerts=(props.alerts||[]).filter(alert=>alert.lot_id===lot.lot_id&&alert.status!=='resolved');
  const reservedIssues = props.admin ? lot.spaces.filter(space => reservedParkingStatus(space)) : [];
  const unknownLocation=activeAlerts.some(alert=>!alert.space_id);
  const mapViewport=useRef(null);
  useEffect(()=>{
    if(!props.legendNearMap||!mapViewport.current)return;
    const el=mapViewport.current;
    const align=()=>{const svg=el.querySelector('svg.pdf-map-svg');if(!svg)return;const box=svg.viewBox.baseVal;const available=Math.max(0,el.clientHeight-28);const rendered=Math.min(available,el.clientWidth*box.height/box.width);el.style.setProperty('--legend-top',`${4+Math.max(0,(available-rendered)/2)}px`)};
    const observer=new ResizeObserver(align);observer.observe(el);align();return()=>observer.disconnect();
  },[lot.lot_id,props.legendNearMap]);
  const [zoomed, setZoomed] = useState(false);
  const [preview,setPreview]=useState(null);
  useEffect(()=>setPreview(null),[lot.lot_id]);
  const [fees,setFees]=useState({});
  const sessions=props.admin ? lot.spaces.filter(space=>space.status==='occupied'&&space.session_id).map(space=>space.session_id).sort().join(',') : '';
  useEffect(()=>{
    if (!props.admin) return;
    let alive=true;
    async function refreshFees(){
      const values=await Promise.all(sessions.split(',').filter(Boolean).map(async id=>{
        try{return [id,await request('/sessions/'+encodeURIComponent(id)+'/fee')]}catch{return [id,null]}
      }));
      if(alive)setFees(Object.fromEntries(values));
    }
    refreshFees();
    const timer=setInterval(refreshFees,15000);
    return()=>{alive=false;clearInterval(timer)};
  },[sessions, props.admin]);

  const kind = lot.lot_id==='C' ? 'airport' : lot.layout_type;
  if (!names[kind]) return <FallbackMap {...props}/>;

  const bankA = lot.spaces.filter(s => s.zone === 'A');
  const bankB = lot.spaces.filter(s => s.zone === 'B');
  const bay = (space, geometry) => {
    const sample=Boolean(preview)&&space.space_id===lot.spaces.find(item=>item.space_type===preview)?.space_id;
    const displayed=sample?{...space,status:'occupied',plate_number:'123가 4567',vehicle_number:'4567',session_id:'preview-only'}:space;
    return <Bay key={space.space_id} {...props} fees={sample?{...fees,'preview-only':{amount_krw:4500,duration_minutes:90}}:fees} space={displayed} {...geometry}/>;
  };
  return <section className={`map-panel pdf-map-panel layout-${kind}`}>
    {activeAlerts.length>0&&<div className="map-alert-banner" role="alert"><strong>⚠ {Math.max(...activeAlerts.map(alert=>Number(alert.level)||1))>=2?t("대피 경보"):t("안전 경보")} · {lot.name}</strong><span>{unknownLocation?t("발생 위치 미확인 · 주차장 전체 주의"):activeAlerts.map(alert=>alert.space_id).join(", ")+t(" 경보 발생")} {t("· 현장 안전 안내를 따라 주세요.")}</span></div>}
    {props.admin && <div className="reserved-parking-status">
      <strong>전용구역 관제</strong>
      {reservedIssues.length ? <ul>{reservedIssues.map(space => <li key={space.space_id}><button type="button" className="button white" onClick={() => props.onSpace(space)}>{space.space_id} · {types[space.space_type]} · {reservedParkingStatus(space)}{space.plate_number ? ' · '+space.plate_number : ''}</button></li>)}</ul> : <span> · 확인할 차량 없음</span>}
    </div>}
    {props.admin && !props.hidePreviewControls && <div className="parking-preview-controls" role="group" aria-label="주차 표시 시연" style={{display:'flex',gap:8,flexWrap:'wrap',marginBottom:12}}>
      {[['general','일반 칸 주차 시연'],['ev','전기차 칸 주차 시연'],['accessible','장애인 칸 주차 시연']].map(([type,title])=><button type="button" key={type} className={'button '+(preview===type?'primary':'white')} disabled={!lot.spaces.some(space=>space.space_type===type)} aria-pressed={preview===type} onClick={()=>setPreview(value=>value===type?null:type)}>{title}</button>)}
      {preview&&<button type="button" className="button white" onClick={()=>setPreview(null)}>시연 종료</button>}
    </div>}
    {preview&&<p role="status" className="fee-note">시연용 샘플 · 선택한 유형의 첫 번째 칸에 123가 4567 / 90분 · 4,500원을 표시합니다. 실제 주차 데이터는 변경되지 않습니다.</p>}
    <div className="map-toolbar"><div className="filter-tabs">{[['all',t('전체 보기')],['empty',t('빈자리')],['ev',t('전기차')],['accessible',t('장애인')]].map(([id,label]) => <button key={id} onClick={() => setFilter(id)} className={filter === id ? 'active' : ''} aria-pressed={filter === id}>{label}</button>)}</div></div>
    <div className="pdf-map-heading">{!props.hideTitle && kind !== 'basic' && <strong>{kind === 'mall' ? t('주차 배치도') : names[kind]}</strong>}<button className="pdf-zoom-button" aria-pressed={zoomed} onClick={() => setZoomed(value => !value)}>{zoomed ? t('전체 도면 보기') : t('주차칸 확대')}</button></div>
    <div ref={mapViewport} data-legend-near-map={props.legendNearMap||undefined} className={`pdf-map-scroll ${zoomed ? 'is-zoomed' : 'fit-map'}`} tabIndex={0} aria-label={t("주차장 도면, 확대 상태에서는 좌우로 이동하세요")}>
      {props.legendNearMap&&<div className="map-legend">{Object.entries(statuses).map(([id,label]) => <span key={id}><i className={`legend-dot ${id}`}/>{label}</span>)}</div>}
      <svg className="pdf-map-svg" viewBox={kind === 'mall' ? '0 0 1536 1024' : kind === 'angled' ? '0 0 1200 760' : kind === 'airport' ? '0 0 1536 1024' : kind === 'loop' ? '0 0 1000 880' : kind === 'l_shape' ? '0 0 900 1100' : '0 0 1000 1040'} role="group" aria-label={t("{0} {1}면 배치도",lot.name,lot.spaces.length)}>
        {kind === 'mall' && <MallParkingMap lot={lot} bay={bay} Arrow={Arrow} Gate={Gate}/>}
        {kind === 'basic' && <>
          <rect className="pdf-road" x="20" y="20" width="960" height="1000" rx="8"/><path className="pdf-boundary" d="M440 995H40V40H960V995H600"/>
          <Zone x={220} y={90} zone="A"/><Zone x={780} y={90} zone="B"/>
          <path className="pdf-center-divider" d="M500 145V900"/>
          {bankA.map((s,i) => bay(s,{points:rectangle(60,145+i*125,300,125),x:210,y:207+i*125,stopPath:`M78 ${175+i*125}v58`}))}
          {bankB.map((s,i) => bay(s,{points:rectangle(640,145+i*125,300,125),x:790,y:207+i*125,stopPath:`M922 ${175+i*125}v58`}))}
          <Arrow x={440} y={370}/><Arrow x={440} y={735}/><Arrow x={560} y={370} rotate={180}/><Arrow x={560} y={735} rotate={180}/>
          <Gate x={440} y={965} label={t("입구")}/><Gate x={560} y={965} label={t("출구")}/>
        </>}
        {kind === 'angled' && <>
          <rect className="pdf-road" x="20" y="20" width="1160" height="720" rx="8"/><path className="pdf-boundary" d="M40 335V40H1160V335M40 425V720H1160V425"/>
          <Zone x={600} y={80} zone="A"/><Zone x={600} y={685} zone="B"/>
          {[bankA,bankB].map((bank,row) => bank.map((s,i) => {const x=65+i*124,y=row?465:125;return bay(s,{points:`${x+75},${y} ${x+199},${y} ${x+124},${y+165} ${x},${y+165}`,x:x+100,y:y+76,labelY:y+25,carX:x+94,carY:y+95,carRotation:Math.atan2(165,-75)*180/Math.PI,vertical:true,stopPath:`M${row?x+36:x+109} ${row?y+150:y+15}h48`});}))}
          <path className="pdf-lane-divider" d="M165 380H1035"/>
          <Arrow x={400} y={380} rotate={90}/><Arrow x={800} y={380} rotate={90}/><Gate x={75} y={380} label={t("입구")}/><Gate x={1125} y={380} label={t("출구")}/>
        </>}
        {kind === 'airport' && <>
          <rect className="pdf-road" x="20" y="20" width="1496" height="984" rx="24"/>
          {[[70, 420, t('국내선 여객청사')], [540, 420, t('국제선 여객청사')], [1010, 450, t('국제화물청사')]].map(([x,w,label]) => <g key={label}>
            <rect className="airport-terminal" x={x} y="52" width={w} height="110" rx="12"/>
            <text className="airport-terminal-text" x={x+w/2} y="117" textAnchor="middle">{label}</text>
          </g>)}
          <path className="airport-shuttle" d="M280 185V245H1235V185"/>
          <path className="airport-route" d="M280 250V365 M750 250V365 M1235 250V365"/>
          <text className="pdf-bay-label" x="750" y="228" textAnchor="middle">{t("청사 순환 셔틀 · 승하차로")}</text>
          {[['A',70,6],['B',540,6],['C',1010,4]].map(([zone,left,columns]) => {
            const spaces = lot.spaces.filter(space => airportZone(space) === zone);
            const width = 420 / columns;
            return <g key={zone}>
              <text className="pdf-bay-label" x={left+210} y="358" textAnchor="middle">{airportZones[zone]} · {spaces.length}{t("면")}</text>
              {spaces.map((space,index) => {
                const x=left+(index%columns)*width,y=385+Math.floor(index/columns)*125;
                return bay(space,{points:rectangle(x,y,width,90),x:x+width/2,y:y+45,vertical:true});
              })}
            </g>;
          })}
          <path className="pdf-lane-divider" d="M170 940H1366"/>
          <Arrow x={455} y={940} rotate={90}/><Arrow x={1040} y={940} rotate={90}/>
          <Gate x={100} y={940} label={t("입구")}/><Gate x={1436} y={940} label={t("출구")}/>
        </>}
        {kind === 'loop' && <>
          <rect className="pdf-road" x="20" y="20" width="960" height="840" rx="8"/><path className="pdf-boundary" d="M155 835H40V40H960V835H845M245 835H755"/>
          <Zone x={500} y={90} zone="A"/><Zone x={500} y={790} zone="B"/>
          {bankA.map((s,i) => bay(s,{points:rectangle(185+i*105,215,105,215),x:237+i*105,y:322,vertical:true,stopPath:`M${212+i*105} 415h50`}))}
          {bankB.map((s,i) => bay(s,{points:rectangle(185+i*105,440,105,215),x:237+i*105,y:547,vertical:true,stopPath:`M${212+i*105} 455h50`}))}
          <Arrow x={110} y={330}/><Arrow x={110} y={645}/><Arrow x={890} y={330} rotate={180}/><Arrow x={890} y={645} rotate={180}/><Arrow x={500} y={145} rotate={90}/><Arrow x={500} y={720} rotate={270}/>
          <Gate x={110} y={800} label={t("입구")}/><Gate x={890} y={800} label={t("출구")}/>
        </>}
        {kind === 'l_shape' && <>
          <path className="pdf-road" d="M25 25H875V1075H480V430H25Z"/><path className="pdf-boundary" d="M45 365V45H855V1055H650M520 1055H500V410H45"/>
          <Zone x={300} y={70} zone="A"/><Zone x={750} y={415} zone="B"/>
          {bankA.map((s,i) => bay(s,{points:rectangle(60+i*112,100,112,220),x:116+i*112,y:210,vertical:true,stopPath:`M${88+i*112} 115h56`}))}
          {bankB.map((s,i) => bay(s,{points:rectangle(650,455+i*110,185,110),x:742,y:510+i*110,stopPath:`M820 ${482+i*110}v55`}))}
          <Arrow x={560} y={900}/><Arrow x={560} y={670}/><Arrow x={560} y={380} rotate={270}/><Arrow x={300} y={380} rotate={270}/>
          <Gate x={560} y={1030} label={t("입구")}/><Gate x={90} y={380} label={t("출구")}/>
        </>}
        <EmergencyExit kind={kind}/>
      </svg>
    </div>
    {!props.legendNearMap&&<div className="map-legend">{Object.entries(statuses).map(([id,label]) => <span key={id}><i className={`legend-dot ${id}`}/>{label}</span>)}</div>}
    <p className="pdf-layout-note"><Icon name="info"/>{kind === 'mall' ? t('신세계 백화점 주차장 B1 가상 배치 · A–D는 신세계 백화점 주차장 내부 구역입니다. 차량번호는 주차칸 확대에서 확인하세요.') : t('첨부 도면 기준 · A/B는 이 주차장 내부 구역입니다.')}<br/>{t("주차칸 선택으로 상세 조회 · 초록색 전기차 / 파란색 장애인")}</p>
  </section>;
}
