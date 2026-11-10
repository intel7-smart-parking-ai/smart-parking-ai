import ToiletMarker from './ToiletMarker.jsx';
import {mallZones,mallRestrooms,mallBayPosition} from './mallParkingLayout.js';

function Facility({x,y=13,label}) {
 return <g className="mall-facility" role="img" aria-label={label}>
  <rect x={x} y={y} width="154" height="44" rx="8"/>
  <text x={x+77} y={y+29} textAnchor="middle">{label}</text>
 </g>;
}

export default function MallParkingMap({lot,bay,Arrow,Gate,t}) {
 return <>
  <rect className="mall-asphalt" width="1536" height="1024"/>
  <g className="mall-wing mall-wing-shopping" aria-label={t("몰 · A / C")}>
   <rect className="mall-wing-floor" x="25" y="70" width="730" height="818" rx="10"/>
   <text className="mall-wing-title" x="58" y="45">{t("몰 · A / C")}</text>
   <Facility x={415} label={t("영화관 ↑")}/>
   <ToiletMarker x={mallRestrooms.shopping} y={10} scale={0.65} label={t("몰 A·C구역 화장실")}/>
   <Facility x={580} label={t("엘리베이터 ↕")}/>
  </g>
  <g className="mall-wing mall-wing-department" aria-label={t("백화점 · B / D")}>
   <rect className="mall-wing-floor" x="781" y="70" width="730" height="818" rx="10"/>
   <text className="mall-wing-title" x="814" y="45">{t("백화점 · B / D")}</text>
   <Facility x={1300} label={t("엘리베이터 ↕")}/>
   <ToiletMarker x={mallRestrooms.department} y={10} scale={0.65} label={t("백화점 B·D구역 화장실")}/>
  </g>
  <path className="mall-outline" d="M78 995H21V68H1515V995H1475M170 995H1365"/>
  <path className="mall-centerline" d="M150 297H1420M116 490H1420M150 722H1420M768 113V872M315 979H1285"/>
  {mallZones.map(([zone,left,top])=><g key={zone} className={`mall-zone mall-zone-${zone}`}>
   <rect className="mall-zone-header" x={left+240} y={top-44} width="120" height="30" rx="12"/>
   <text className="mall-zone-name" x={left+300} y={top-22} textAnchor="middle">{t("{0}구역",zone)}</text>
   {lot.spaces.filter(s=>s.zone===zone).map((space,i)=>{
    const {x,y,h}=mallBayPosition(left,top,i);
    return bay(space,{points:`${x},${y} ${x+75},${y} ${x+75},${y+h} ${x},${y+h}`,x:x+37.5,y:y+40,vertical:true,labelY:y+28,carY:y+66,stopPath:`M${x+18} ${y+(i>=8?h-16:15)}h39`});
   })}
  </g>)}
  {[[435,297,90],[1125,297,90],[410,490,270],[1125,490,90],[435,722,90],[1125,722,90],[68,290,0],[68,728,0],[1472,290,180],[1472,728,180],[768,310,180],[768,725,0],[250,979,90],[1320,979,90]].map(([x,y,rotate])=><Arrow key={`${x}-${y}`} x={x} y={y} rotate={rotate}/>)}
  {[['shopping',119,'몰 연결 출입구'],['department',822,'백화점 연결 출입구']].map(([wing,left,label])=><g key={wing} className={`mall-wing-${wing}`}>
   <g className="mall-crossing">{Array.from({length:5},(_,i)=><rect key={i} x={left+274+i*12} y="879" width="6" height="24"/>)}</g>
   <rect className="mall-concourse" x={left} y="905" width="600" height="56" rx="10"/>
   <text className="mall-concourse-label" x={left+24} y="942">{t(label)}</text>
   <Facility x={left+424} y={911} label={t("에스컬레이터 ↑")}/>
  </g>)}
  <Gate x={123} y={979} label={t("차량 입구")}/><Gate x={1417} y={979} label={t("차량 출구")}/>
 </>;
}
