import {useEffect,useRef} from 'react';
import BrandWordmark from './BrandWordmark.jsx';
import {t} from './serviceLocale.js';
import './styles/welcome-promo.css';

const promoHiddenKey='parkon-welcome-promo-blue-hidden';
const promoDate=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'});
let hiddenDate;
export default function WelcomePromo({className='',autoOpen=false,previewOnce=false}){
 const promoDialog=useRef(null);
 const benefitsDialog=useRef(null);
 const hideToday=useRef(null);
 function openPromo(ignoreHidden=false){
  const dialog=promoDialog.current;
  if(!dialog)return;
  const today=promoDate.format();
  if(!ignoreHidden){
   try{if(localStorage.getItem(promoHiddenKey)===today)return}catch{if(hiddenDate===today)return}
  }
  if(hideToday.current)hideToday.current.checked=false;
  if(!dialog.open)dialog.showModal();
 }
 useEffect(()=>{
  if(autoOpen||previewOnce)openPromo(previewOnce);
 },[autoOpen,previewOnce]);
 function setHiddenToday({target}){
  hiddenDate=target.checked?promoDate.format():undefined;
  try{
   if(hiddenDate)localStorage.setItem(promoHiddenKey,hiddenDate);
   else localStorage.removeItem(promoHiddenKey);
  }catch{/* Keep the preference for this page when storage is unavailable. */}
 }
 return <>
  <button type="button" className={`welcome-promo-trigger ${className}`} aria-label="PARK:ON 혜택 안내" aria-haspopup="dialog" onClick={()=>openPromo()}><BrandWordmark/></button>
  <dialog className="welcome-promo" ref={promoDialog} aria-labelledby="welcome-promo-title">
   <div className="welcome-promo-poster" lang="ko">
    <BrandWordmark showCar={false}/>
    <h2 id="welcome-promo-title">첫 결제부터<br/><strong>적립 혜택까지!</strong></h2>
    <p>빈자리 확인, 내 차 찾기, 주차요금 결제를 한곳에서.</p>
    <p className="welcome-promo-example">이벤트 예시 · 시연용 화면이며 실제 적립은 없습니다</p>
    <button type="button" className="welcome-promo-badge" aria-haspopup="dialog" onClick={()=>benefitsDialog.current?.showModal()}>첫 결제 혜택 · 적립 안내</button>
    <div className="welcome-promo-mark" aria-hidden="true">P</div>
   </div>
   <form method="dialog">
    <label><input type="checkbox" ref={hideToday} onChange={setHiddenToday}/> 오늘 하루 동안 보지 않기</label>
    <button type="submit" autoFocus>{t('닫기')}</button>
   </form>
  </dialog>
  <dialog className="welcome-promo welcome-benefits" ref={benefitsDialog} aria-labelledby="welcome-benefits-title" lang="ko">
   <div className="welcome-benefits-content">
    <h2 id="welcome-benefits-title">첫 결제 혜택 · 적립 안내</h2>
    <button type="submit" form="welcome-benefits-actions" className="welcome-benefits-dismiss" aria-label="혜택 안내 닫기">×</button>
    <dl>
     <dt>첫 결제 혜택</dt><dd>첫 결제 시 2,000포인트 적립</dd>
     <dt>기본 적립</dt><dd>주차요금 결제 금액의 1% 적립</dd>
    </dl>
    <p className="welcome-promo-example">이벤트 예시입니다. 시연용 화면으로, 실제 포인트 적립·지급 기능은 없습니다.</p>
   </div>
   <form method="dialog" id="welcome-benefits-actions"><button type="submit" autoFocus>닫기</button></form>
  </dialog>
 </>;
}
