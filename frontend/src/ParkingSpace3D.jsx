import {useEffect,useRef,useState} from 'react';
import {spaceGeometry3d} from './spaceGeometry3d.js';
import {statuses,types} from './components.jsx';
import './styles/space3d.css';

export default function ParkingSpace3D({lot,filter,alerts=[],found,onSpace,variant}){
 const geometry=spaceGeometry3d(lot);
 const stage=useRef(null),drag=useRef(null),pointers=useRef(new Map()),pinch=useRef(null),suppressClick=useRef(false),zoomRef=useRef(1);
 const [width,setWidth]=useState(700),[rotation,setRotation]=useState(-24),[tilt,setTilt]=useState(46),[zoom,setZoom]=useState(1);
 function changeZoom(value){const next=Math.max(.5,Math.min(4,value));zoomRef.current=next;setZoom(next)}
 useEffect(()=>{const observer=new ResizeObserver(([entry])=>setWidth(entry.contentRect.width));observer.observe(stage.current);return()=>observer.disconnect()},[]);
 useEffect(()=>{
  const element=stage.current;
  function wheel(event){event.preventDefault();const delta=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?element.clientHeight:1);changeZoom(zoomRef.current*Math.exp(-Math.max(-200,Math.min(200,delta))*.002))}
  element.addEventListener('wheel',wheel,{passive:false});
  return()=>element.removeEventListener('wheel',wheel);
 },[]);
 function reset(){pointers.current.clear();pinch.current=null;drag.current=null;setRotation(variant==='soft'?-24:0);setTilt(variant==='soft'?46:35);changeZoom(1)}
 function distance(){const [a,b]=[...pointers.current.values()];return Math.hypot(a.x-b.x,a.y-b.y)}
 function pointerDown(event){
  if(event.pointerType==='mouse'&&event.button!==0)return;
  if(!pointers.current.size)suppressClick.current=false;
  pointers.current.set(event.pointerId,{x:event.clientX,y:event.clientY});
  // Capture on the original button so an ordinary tap still opens that bay.
  (event.target.closest('button')||event.currentTarget).setPointerCapture(event.pointerId);
  if(pointers.current.size===1)drag.current={x:event.clientX,y:event.clientY,rotation};
  else if(pointers.current.size===2){pinch.current={distance:Math.max(1,distance()),zoom:zoomRef.current};drag.current=null;suppressClick.current=true}
 }
 function pointerMove(event){
  if(!pointers.current.has(event.pointerId))return;
  pointers.current.set(event.pointerId,{x:event.clientX,y:event.clientY});
  if(pointers.current.size>=2&&pinch.current){changeZoom(pinch.current.zoom*distance()/pinch.current.distance);return}
  if(drag.current){const dx=event.clientX-drag.current.x,dy=event.clientY-drag.current.y;if(Math.hypot(dx,dy)>6)suppressClick.current=true;if(suppressClick.current)setRotation(Math.max(-80,Math.min(80,drag.current.rotation+dx*.3)))}
 }
 function pointerEnd(event){
  if(!pointers.current.delete(event.pointerId))return;
  pinch.current=null;drag.current=null;
  if(pointers.current.size===1){const [point]=pointers.current.values();drag.current={...point,rotation}}
 }
 useEffect(()=>{reset()},[variant]);
 if(!geometry)return <p>이 배치의 3D 보기는 준비 중입니다. 2D 도면을 이용해 주세요.</p>;
 const {width:gw,height:gh,spaces}=geometry;
 const scale=Math.min((width-60)/(gw*.95+gh*.45),430/(gh*.8+gw*.35))*zoom;
 return <div className={`space3d space3d-${variant}`}>
  <div className="space3d-heading"><div><span>{variant==='soft'?'CONCEPT A / DAYLIGHT':'CONCEPT B / CONTROL ROOM'}</span><h3>{variant==='soft'?'한눈에 보는 입체 주차장':'실시간 공간 관제'}<small>{lot.name}</small></h3></div><span className="space3d-source">API 상태 연동</span></div>
  <div className="space3d-stage" ref={stage} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerEnd} onPointerCancel={pointerEnd} onLostPointerCapture={pointerEnd} onClickCapture={event=>{if(event.detail!==0&&suppressClick.current){event.preventDefault();event.stopPropagation()}}}>
   <div className="space3d-center"><div className="space3d-world" style={{width:gw,height:gh,'--scene-scale':scale,'--scene-tilt':`${tilt}deg`,'--scene-rotation':`${rotation}deg`}}>
    <div className="space3d-slab"/><div className="space3d-floor"><span className="space3d-floor-title">PARK : ON / {lot.lot_id}</span></div>
    {spaces.map(s=>{
     const warned=alerts.some(a=>a.lot_id===lot.lot_id&&a.space_id===s.space_id&&a.status!=='resolved');
     const selected=found?.lot_id===lot.lot_id&&found?.space_id===s.space_id;
     const dim=filter!=='all'&&!(filter==='empty'?s.status==='empty':filter.startsWith('source:')?s.source===filter.slice(7):s.space_type===filter);
     const car=s.status==='occupied';
     const carWidth=Math.min(s.rotation===90?s.h*.56:s.w*.55,68),carLength=carWidth*1.85;
     return <button key={s.space_id} className={`space3d-bay ${s.status} ${s.space_type} ${dim?'dim':''} ${selected?'found':''} ${warned?'warned':''}`} style={{left:s.x,top:s.y,width:s.w,height:s.h}} onClick={()=>onSpace(s)} aria-label={`${s.space_id} ${types[s.space_type]||''} ${statuses[s.status]}${warned?' 경보':''}`}>
      <span className="space3d-bay-ground" style={s.slant?{clipPath:`polygon(${s.slant/s.w*100}% 0,100% 0,${(s.w-s.slant)/s.w*100}% 100%,0 100%)`}:undefined}/><span className="space3d-bay-id">{s.space_id}</span>
      {car?<span className="space3d-car" style={{width:carWidth,height:carLength,'--car-rotation':`${s.rotation}deg`}} aria-hidden="true"><i className="car3d-side left"/><i className="car3d-side right"/><i className="car3d-end front"/><i className="car3d-end back"/><i className="car3d-top"><b/><em/><b/></i></span>:<span className="space3d-bay-state">{s.status==='empty'?'P':statuses[s.status]}</span>}
      {car&&<span className="space3d-car-number">{lot.type==='real'?s.car_id:s.vehicle_number||s.plate_number||'주차 중'}</span>}<span className="space3d-bay-type">{s.space_type==='ev'?'⚡':s.space_type==='accessible'?'♿':''}</span>{warned&&<span className="space3d-alert">!</span>}
     </button>
    })}
   </div></div>
   <span className="space3d-hint">휠 / 두 손가락으로 확대·축소 · 드래그로 회전 · 주차칸 선택</span>
  </div>
  <div className="space3d-controls"><label>회전<input type="range" min="-80" max="80" value={rotation} onChange={e=>setRotation(+e.target.value)}/></label><label>기울기<input type="range" min="0" max="60" value={tilt} onChange={e=>setTilt(+e.target.value)}/></label><label>확대<input type="range" min="0.5" max="4" step="0.01" value={zoom} onChange={e=>changeZoom(+e.target.value)}/></label><button onClick={reset}>시점 초기화 ↺</button></div>
  <p className="space3d-note">기존 도면의 주차칸 배치와 조회 상태를 입체로 표시합니다. 차량은 점유 표시용 모형이며 실제 차종·높이·건물 구조를 재현하지 않습니다.</p>
 </div>
}
