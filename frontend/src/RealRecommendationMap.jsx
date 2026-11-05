import {useEffect,useRef,useState} from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
maplibregl.setWorkerUrl(workerUrl);
export default function RealRecommendationMap({locations,selected,onSelect}){
 const container=useRef(null),mapRef=useRef(null),markers=useRef([]);
 const [ready,setReady]=useState(false),[error,setError]=useState(''),[retry,setRetry]=useState(0);
 useEffect(()=>{
  let map,alive=true;setReady(false);setError('');
  try{
   map=new maplibregl.Map({container:container.current,style:'https://tiles.openfreemap.org/styles/bright',center:[129.04,35.115],zoom:15.6,pitch:50,bearing:-20,canvasContextAttributes:{antialias:true}});
   mapRef.current=map;map.addControl(new maplibregl.NavigationControl({visualizePitch:true}),'top-right');
   map.addControl(new maplibregl.ScaleControl({unit:'metric'}));
   map.on('error',()=>{if(alive)setError('지도 일부를 불러오지 못했습니다. 인터넷 연결을 확인하거나 시연용 지도를 이용하세요.')});
   map.on('load',()=>{if(!alive)return;try{
    map.addSource('parking-buildings',{type:'vector',url:'https://tiles.openfreemap.org/planet'});
    const before=map.getStyle().layers.find(layer=>layer.type==='symbol'&&layer.layout?.['text-field'])?.id;
    map.addLayer({id:'parking-buildings-3d',type:'fill-extrusion',source:'parking-buildings','source-layer':'building',minzoom:14,filter:['!=',['get','hide_3d'],true],paint:{'fill-extrusion-color':'#b8c9dd','fill-extrusion-height':['coalesce',['get','render_height'],3],'fill-extrusion-base':['coalesce',['get','render_min_height'],0],'fill-extrusion-opacity':.85}},before);
    setReady(true);setError('');
   }catch{setError('건물 입체 표시에 실패했습니다. 기본 지도는 계속 이용할 수 있습니다.');setReady(true)}});
  }catch{setError('이 브라우저에서 3D 지도를 시작하지 못했습니다. 시연용 지도를 이용하세요.')}
  const observer=new ResizeObserver(()=>map?.resize());observer.observe(container.current);
  return()=>{alive=false;observer.disconnect();markers.current.forEach(m=>m.remove());markers.current=[];map?.remove();mapRef.current=null};
 },[retry]);
 useEffect(()=>{
  if(!ready||!mapRef.current)return;
  markers.current.forEach(m=>m.remove());
  markers.current=locations.map(lot=>{const el=document.createElement('button');el.type='button';el.className='recommend-real-pin';el.textContent=`P · ${lot.name}`;el.setAttribute('aria-label',`${lot.name} 위치 선택`);el.addEventListener('click',()=>onSelect(lot.lot_id));return new maplibregl.Marker({element:el}).setLngLat(lot.coordinates).addTo(mapRef.current)});
  if(locations.length>1){const bounds=new maplibregl.LngLatBounds();locations.forEach(l=>bounds.extend(l.coordinates));mapRef.current.fitBounds(bounds,{padding:70,maxZoom:16,duration:0})}
  return()=>{markers.current.forEach(m=>m.remove());markers.current=[]};
 },[ready,locations,onSelect]);
 useEffect(()=>{const lot=locations.find(l=>l.lot_id===selected);if(ready&&lot)mapRef.current?.flyTo({center:lot.coordinates,zoom:16.5,pitch:55,duration:600,essential:false})},[ready,selected,locations]);
 return <><div className="recommend-real-map" ref={container} role="region" aria-label="실제 위치 3D 지도"/>{!ready&&!error&&<p role="status">실제 지도를 불러오는 중…</p>}{error&&<p role="status" className="recommend-error">{error}<button onClick={()=>setRetry(v=>v+1)}>다시 불러오기</button></p>}<p className="recommend-note">{locations.length?'등록된 좌표에 주차장 위치를 표시합니다.':'등록된 주차장 좌표가 없어 부산 지역을 미리 보여줍니다. 아래에서 정확한 위치를 등록하세요.'} 건물 높이와 형태는 공개 지도 데이터에 따라 실제와 다를 수 있습니다. 휠·핀치로 확대 / 우클릭 드래그·두 손가락으로 회전합니다.</p></>
}
