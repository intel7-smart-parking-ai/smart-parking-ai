import {useState} from 'react';
import {Icon, ParkingMap as FallbackMap, statuses, types} from './components.jsx';
import './styles/layout-maps.css';
import MallParkingMap from './MallParkingMap.jsx';

const names = {basic: '기본형', angled: '사선형', loop: '일방순환형', l_shape: 'L자복합형', mall: '신세계백화점주차장 B1 · 대형 주차장'};
function Arrow({x, y, rotate = 0}) {
  return <g className="pdf-road-arrow" transform={`translate(${x} ${y}) rotate(${rotate})`} aria-hidden="true"><path d="M0 28V-23M-12-9 0-23 12-9"/></g>;
}
function Zone({x, y, zone}) {
  return <g className={`pdf-zone zone-${zone}`} transform={`translate(${x} ${y})`} aria-hidden="true"><rect x="-65" y="-17" width="130" height="34" rx="12"/><text textAnchor="middle" y="6">{zone}구역</text></g>;
}
function Gate({x, y, label}) {
  return <g className="pdf-gate" transform={`translate(${x} ${y})`} aria-hidden="true"><rect x="-42" y="-18" width="84" height="36" rx="6"/><text textAnchor="middle" y="6">{label}</text></g>;
}
function MiniCar({x, y, number, rotation = 0}) {
  const label = String(number ?? '');
  const badgeWidth = Math.max(64, label.length * 20 + 16);
  return <g transform={`translate(${x} ${y})`} aria-hidden="true"><g transform={`rotate(${rotation}) scale(1.2)`}><g className="pdf-car-wheels"><rect x="-30" y="-19" width="15" height="9" rx="3"/><rect x="16" y="-19" width="15" height="9" rx="3"/><rect x="-30" y="10" width="15" height="9" rx="3"/><rect x="16" y="10" width="15" height="9" rx="3"/></g><rect className="pdf-car-body" x="-42" y="-15" width="84" height="30" rx="12"/><rect className="pdf-car-window" x="-23" y="-12" width="46" height="24" rx="5"/></g><rect className="pdf-car-plate" x={-badgeWidth / 2} y="-22" width={badgeWidth} height="44" rx="8"/><text className="pdf-car-number" style={{fontSize:label.length > 2 ? 28 : 34}} textAnchor="middle" dominantBaseline="central" y="0">{label}</text></g>;
}
function Bay({space, points, x, y, vertical, labelY, carY, carX, carRotation, stopPath, filter, alerts, found, lot, onSpace}) {
  const dim = filter !== 'all' && !(filter === 'empty' ? space.status === 'empty' : filter.startsWith('source:')?space.source===filter.slice(7):space.space_type === filter);
  const warned = alerts.some(a => a.lot_id === lot.lot_id && a.space_id === space.space_id && a.status !== 'resolved');
  const selected = found?.lot_id === lot.lot_id && found?.space_id === space.space_id;
  const number = lot.type === 'real' ? space.car_id : space.vehicle_number;
  const label = `${space.space_id} ${types[space.space_type]} ${statuses[space.status]}${(space.car_id||number) ? ' 차량 ' + (lot.type === 'real' ? space.car_id : space.plate_number || number) : ''}`;
  return <g className={`pdf-bay ${space.status} ${space.space_type} ${dim ? 'dim' : ''} ${selected ? 'found' : ''} ${warned ? 'warned' : ''}`} role="button" tabIndex={0} aria-label={label} onClick={() => onSpace(space)} onKeyDown={e => {if (e.key === 'Enter' || e.key === ' ') {e.preventDefault(); onSpace(space);}}}>
    <title>{label}</title><polygon className="pdf-bay-shape" points={points}/>
    {stopPath && <path className="pdf-wheel-stop" d={stopPath} aria-hidden="true"/>}
    <text className="pdf-bay-label" x={x} y={labelY ?? y - (vertical ? 75 : 22)} textAnchor="middle">{space.space_id}</text>
    {(space.car_id||number) ? <g transform={lot.layout_type === 'mall' ? `translate(${carX ?? x} ${carY ?? y}) scale(.68) translate(${- (carX ?? x)} ${- (carY ?? y)})` : undefined}><MiniCar x={carX ?? x} y={carY ?? y + (vertical ? 2 : 11)} number={number} rotation={carRotation ?? (vertical ? 90 : 0)}/></g> : <text className="pdf-bay-status" x={x} y={carY ?? y + 12} textAnchor="middle">{statuses[space.status]}</text>}
    {space.space_type === 'ev' && <text className="pdf-bay-special" x={x} y={y + (vertical ? 68 : 42)} textAnchor="middle">⚡ 전기차</text>}
    {space.space_type === 'accessible' && <text className="pdf-bay-special" x={x} y={y + (vertical ? 68 : 42)} textAnchor="middle">♿ 장애인</text>}
    {warned && <text className="pdf-warning" x={x + 30} y={y - 45}>!</text>}
  </g>;
}
const rectangle = (x, y, w, h) => `${x},${y} ${x+w},${y} ${x+w},${y+h} ${x},${y+h}`;

export default function ParkingLayoutMap(props) {
  const {lot, filter, setFilter} = props;
  const [zoomed, setZoomed] = useState(false);
  if (!names[lot.layout_type]) return <FallbackMap {...props}/>;
  const kind = lot.layout_type;
  const bankA = lot.spaces.filter(s => s.zone === 'A');
  const bankB = lot.spaces.filter(s => s.zone === 'B');
  const bay = (space, geometry) => <Bay key={space.space_id} {...props} space={space} {...geometry}/>;
  return <section className={`map-panel pdf-map-panel layout-${kind}`}>
    <div className="map-toolbar"><div className="filter-tabs">{[['all','전체 보기'],['empty','빈자리'],['ev','전기차'],['accessible','장애인']].map(([id,label]) => <button key={id} onClick={() => setFilter(id)} className={filter === id ? 'active' : ''} aria-pressed={filter === id}>{label}</button>)}</div></div>
    <div className="pdf-map-heading"><strong>{names[kind]} <span>{lot.spaces.length}면</span></strong><button className="pdf-zoom-button" aria-pressed={zoomed} onClick={() => setZoomed(value => !value)}>{zoomed ? '전체 도면 보기' : '주차칸 확대'}</button></div>
    <div className={`pdf-map-scroll ${zoomed ? 'is-zoomed' : 'fit-map'}`} tabIndex={0} aria-label="주차장 도면, 확대 상태에서는 좌우로 이동하세요">
      <svg className="pdf-map-svg" viewBox={kind === 'mall' ? '0 0 1536 1024' : kind === 'angled' ? '0 0 1200 760' : kind === 'loop' ? '0 0 1000 880' : kind === 'l_shape' ? '0 0 900 1100' : '0 0 1000 1040'} role="group" aria-label={`${lot.name} ${lot.spaces.length}면 배치도`}>
        {kind === 'mall' && <MallParkingMap lot={lot} bay={bay} Arrow={Arrow} Gate={Gate}/>}
        {kind === 'basic' && <>
          <rect className="pdf-road" x="20" y="20" width="960" height="1000" rx="8"/><path className="pdf-boundary" d="M440 995H40V40H960V995H600"/>
          <Zone x={220} y={90} zone="A"/><Zone x={780} y={90} zone="B"/>
          <path className="pdf-center-divider" d="M500 145V900"/>
          {bankA.map((s,i) => bay(s,{points:rectangle(60,145+i*125,300,125),x:210,y:207+i*125,stopPath:`M78 ${175+i*125}v58`}))}
          {bankB.map((s,i) => bay(s,{points:rectangle(640,145+i*125,300,125),x:790,y:207+i*125,stopPath:`M922 ${175+i*125}v58`}))}
          <Arrow x={440} y={370}/><Arrow x={440} y={735}/><Arrow x={560} y={370} rotate={180}/><Arrow x={560} y={735} rotate={180}/>
          <Gate x={440} y={965} label="입구"/><Gate x={560} y={965} label="출구"/>
        </>}
        {kind === 'angled' && <>
          <rect className="pdf-road" x="20" y="20" width="1160" height="720" rx="8"/><path className="pdf-boundary" d="M40 335V40H1160V335M40 425V720H1160V425"/>
          <Zone x={600} y={80} zone="A"/><Zone x={600} y={685} zone="B"/>
          {[bankA,bankB].map((bank,row) => bank.map((s,i) => {const x=65+i*124,y=row?465:125;return bay(s,{points:`${x+75},${y} ${x+199},${y} ${x+124},${y+165} ${x},${y+165}`,x:x+100,y:y+76,labelY:y+25,carX:x+94,carY:y+95,carRotation:Math.atan2(165,-75)*180/Math.PI,vertical:true,stopPath:`M${row?x+36:x+109} ${row?y+150:y+15}h48`});}))}
          <path className="pdf-lane-divider" d="M165 380H1035"/>
          <Arrow x={400} y={380} rotate={90}/><Arrow x={800} y={380} rotate={90}/><Gate x={75} y={380} label="입구"/><Gate x={1125} y={380} label="출구"/>
        </>}
        {kind === 'loop' && <>
          <rect className="pdf-road" x="20" y="20" width="960" height="840" rx="8"/><path className="pdf-boundary" d="M155 835H40V40H960V835H845M245 835H755"/>
          <Zone x={500} y={90} zone="A"/><Zone x={500} y={790} zone="B"/>
          {bankA.map((s,i) => bay(s,{points:rectangle(185+i*105,215,105,215),x:237+i*105,y:322,vertical:true,stopPath:`M${212+i*105} 415h50`}))}
          {bankB.map((s,i) => bay(s,{points:rectangle(185+i*105,440,105,215),x:237+i*105,y:547,vertical:true,stopPath:`M${212+i*105} 455h50`}))}
          <Arrow x={110} y={330}/><Arrow x={110} y={645}/><Arrow x={890} y={330} rotate={180}/><Arrow x={890} y={645} rotate={180}/><Arrow x={500} y={145} rotate={90}/><Arrow x={500} y={720} rotate={270}/>
          <Gate x={110} y={800} label="입구"/><Gate x={890} y={800} label="출구"/>
        </>}
        {kind === 'l_shape' && <>
          <path className="pdf-road" d="M25 25H875V1075H480V430H25Z"/><path className="pdf-boundary" d="M45 365V45H855V1055H650M520 1055H500V410H45"/>
          <Zone x={300} y={70} zone="A"/><Zone x={750} y={415} zone="B"/>
          {bankA.map((s,i) => bay(s,{points:rectangle(60+i*112,100,112,220),x:116+i*112,y:210,vertical:true,stopPath:`M${88+i*112} 115h56`}))}
          {bankB.map((s,i) => bay(s,{points:rectangle(650,455+i*110,185,110),x:742,y:510+i*110,stopPath:`M820 ${482+i*110}v55`}))}
          <Arrow x={560} y={900}/><Arrow x={560} y={670}/><Arrow x={560} y={380} rotate={270}/><Arrow x={300} y={380} rotate={270}/>
          <Gate x={560} y={1030} label="입구"/><Gate x={90} y={380} label="출구"/>
        </>}
      </svg>
    </div>
    <div className="map-legend">{Object.entries(statuses).map(([id,label]) => <span key={id}><i className={`legend-dot ${id}`}/>{label}</span>)}</div>
    <p className="pdf-layout-note"><Icon name="info"/>{kind === 'mall' ? '신세계백화점주차장 B1 가상 배치 · A–D는 신세계백화점주차장 내부 구역입니다. 차량번호는 주차칸 확대에서 확인하세요.' : '첨부 도면 기준 · A/B는 이 주차장 내부 구역입니다.'}<br/>주차칸 선택으로 상세 조회 · 초록색 전기차 / 파란색 장애인</p>
  </section>;
}
