import {Component,lazy,Suspense,useEffect,useMemo,useState} from 'react';
import {loadLots} from './api.js';
import {readLotCoordinates,validCoordinates} from './recommendationMapData.js';
import DemoRecommendationMap from './DemoRecommendationMap.jsx';
import './styles/recommendation-map.css';
const RealMap=lazy(()=>import('./RealRecommendationMap.jsx'));
class MapBoundary extends Component{
 state={failed:false};
 static getDerivedStateFromError(){return {failed:true}}
 componentDidCatch(){this.props.onUnavailable?.()}
 render(){return this.state.failed?<p role="status">실제 지도 모듈을 불러오지 못했습니다. 시연용 지도로 전환하거나 새로고침 후 다시 시도하세요.</p>:this.props.children}
}
export default function RecommendationMap({recommended=null}){
 const [mode,setMode]=useState('demo'),[lots,setLots]=useState(recommended||[]),[selected,setSelected]=useState(recommended?.[0]?.lot_id||''),[error,setError]=useState(''),[loading,setLoading]=useState(!recommended),[reload,setReload]=useState(0);
 const [mapNotice,setMapNotice]=useState('');
 function fallback(){setMode('demo');setMapNotice('실제 지도 연결에 실패해 시연용 3D 지도로 전환했습니다.');}
 useEffect(()=>{function offline(){fallback()}window.addEventListener('offline',offline);return()=>window.removeEventListener('offline',offline)},[]);
 const [overrides,setOverrides]=useState(()=>{try{return JSON.parse(localStorage.getItem('parkon-lot-coordinates')||'{}')||{}}catch{return {}}});
 const [lat,setLat]=useState(''),[lng,setLng]=useState(''),[coordinateError,setCoordinateError]=useState('');
 useEffect(()=>{if(recommended){setLots(recommended);return}let alive=true;setLoading(true);loadLots('team').then(data=>{if(alive){setLots(data);setSelected(old=>data.some(l=>l.lot_id===old)?old:data[0]?.lot_id||'');setError('')}}).catch(()=>{if(alive)setError('주차장 목록을 가져오지 못했습니다. 잠시 후 다시 시도하세요.')}).finally(()=>{if(alive)setLoading(false)});return()=>{alive=false}},[recommended,reload]);
 const locations=useMemo(()=>lots.flatMap(l=>{const coordinates=readLotCoordinates(l,overrides);return coordinates?[{...l,coordinates}]:[]}),[lots,overrides]);
 const lot=lots.find(l=>l.lot_id===selected);
 useEffect(()=>{const coordinates=lot&&readLotCoordinates(lot,overrides);setLng(coordinates?String(coordinates[0]):'');setLat(coordinates?String(coordinates[1]):'');setCoordinateError('')},[lot,overrides]);
 function save(e){e.preventDefault();const coordinates=[Number(lng),Number(lat)];if(!lng.trim()||!lat.trim()||!validCoordinates(coordinates)){setCoordinateError('올바른 경도(-180~180)와 위도(-90~90)를 입력하세요.');return}const value={...overrides,[selected]:coordinates};setOverrides(value);try{localStorage.setItem('parkon-lot-coordinates',JSON.stringify(value));setCoordinateError('이 브라우저에 위치를 저장했습니다.')}catch{setCoordinateError('현재 화면에 적용했습니다. 브라우저 저장소에는 저장하지 못했습니다.')}}
 return <section className="recommend-map" aria-label={recommended?'추천 주차장 지도':'주차장 지도'}><header className="recommend-map-heading"><div><h3>{recommended?'추천 주차장 지도':'주차장 위치 미리보기'}</h3><p>{recommended?'챗봇이 추천한 주차장을 두 가지 지도로 비교하세요.':'전체 주차장을 표시합니다. 챗봇의 추천 결과가 아닙니다.'}</p></div><div className="recommend-map-tabs" aria-label="지도 모드"><button aria-pressed={mode==='demo'} onClick={()=>setMode('demo')}>시연용 3D</button><button aria-pressed={mode==='real'} onClick={()=>{if(navigator.onLine===false){fallback();return}setMapNotice('');setMode('real')}}>실제 위치 3D</button></div></header>
 {mapNotice&&<p role="status">{mapNotice}</p>}
 {loading&&<p role="status">주차장 목록을 불러오는 중…</p>}{error&&<p role="status">{error}<button onClick={()=>setReload(v=>v+1)}>다시 시도</button></p>}
 {!!lots.length&&<><div className="recommend-lots" aria-label="지도 주차장 선택">{lots.map(l=><button key={l.lot_id} aria-pressed={selected===l.lot_id} onClick={()=>setSelected(l.lot_id)}><b>{l.name}</b><span>{Number.isInteger(l.empty_spaces)?`빈자리 ${l.empty_spaces}`:'빈자리 확인 필요'}</span></button>)}</div>
 {mode==='demo'?<DemoRecommendationMap lots={lots} selected={selected} onSelect={setSelected}/>:<><MapBoundary onUnavailable={fallback}><Suspense fallback={<p role="status">3D 지도 준비 중…</p>}><RealMap onUnavailable={fallback} locations={locations} selected={selected} onSelect={setSelected}/></Suspense></MapBoundary>{lot&&<details className="recommend-coordinate"><summary>{readLotCoordinates(lot,overrides)?`${lot.name} · 등록 위치 확인`:`${lot.name} · 실제 위치 등록 필요`}</summary><form onSubmit={save}><label>경도<input type="number" min="-180" max="180" step="any" required value={lng} onChange={e=>setLng(e.target.value)}/></label><label>위도<input type="number" min="-90" max="90" step="any" required value={lat} onChange={e=>setLat(e.target.value)}/></label><button type="submit">위치 적용</button></form><p>정확히 확인한 좌표를 입력하세요. 이 브라우저에만 저장되며 서버 데이터는 변경하지 않습니다.</p>{coordinateError&&<p role="status">{coordinateError}</p>}</details>}</>}
 <p className="recommend-note">빈자리는 {recommended?'챗봇 응답':'목록 조회'} 시점 기준이며 예약이나 이동 경로를 뜻하지 않습니다. 다른 주차장으로 이동하기 전에 상태를 다시 확인하세요.</p></>}
 </section>
}
