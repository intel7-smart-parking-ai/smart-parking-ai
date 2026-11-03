export default function MallParkingMap({lot,bay,Arrow,Gate}) {
 return <>
  <rect className="mall-asphalt" width="1536" height="1024"/>
  <text className="mall-title" x="40" y="43">신세계백화점주차장 · 64면</text>
  <path className="mall-outline" d="M78 995H21V68H1515V995H1475M170 995H1365"/>
  <path className="mall-centerline" d="M150 297H1420M116 490H1420M150 722H1420M768 113V872M315 955H610M945 955H1285"/>
  {[['A',119,134],['B',822,134],['C',119,570],['D',822,570]].map(([zone,left,top])=><g key={zone} className={`mall-zone mall-zone-${zone}`}>
   <rect className="mall-zone-header" x={left+240} y={top-44} width="120" height="30" rx="12"/>
   <text className="mall-zone-name" x={left+300} y={top-22} textAnchor="middle">{zone}구역</text>
   {lot.spaces.filter(s=>s.zone===zone).map((space,i)=>{
    const col=i%8,row=Math.floor(i/8),x=left+col*75,y=top+row*(zone==='A'||zone==='B'?191:182),h=row?120:132;
    return bay(space,{points:`${x},${y} ${x+75},${y} ${x+75},${y+h} ${x},${y+h}`,x:x+37.5,y:y+40,vertical:true,labelY:y+28,carY:y+66,stopPath:`M${x+18} ${y+(row?h-16:15)}h39`});
   })}
  </g>)}
  {[[435,297,90],[1125,297,90],[410,490,270],[1125,490,90],[435,722,90],[1125,722,90],[68,290,0],[68,728,0],[1472,290,180],[1472,728,180],[768,310,180],[768,725,0],[250,955,90],[1320,955,90]].map(([x,y,rotate])=><Arrow key={`${x}-${y}`} x={x} y={y} rotate={rotate}/>)}
  <g className="mall-crossing">{Array.from({length:6},(_,i)=><rect key={i} x={729+i*14} y="886" width="7" height="34"/>)}</g>
  <g className="mall-store"><rect x="634" y="928" width="270" height="54"/><text x="769" y="963" textAnchor="middle">보행자 출입구</text></g>
  <Gate x={123} y={955} label="입구"/><Gate x={1417} y={955} label="출구"/>
 </>;
}
