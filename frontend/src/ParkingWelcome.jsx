import ParkingHelp from './ParkingHelp.jsx';
import BrandWordmark from './BrandWordmark.jsx';
import WelcomePromo from './WelcomePromo.jsx';
import ChargingStatus,{ChargingIcon} from './ChargingStatus.jsx';
import {getLanguage,translate} from './serviceLocale.js';
import {useEffect,useRef,useState} from 'react';
import {welcomeLanguages,welcomeText,welcomeHubText} from './welcomeLocales.js';
import {Icon,ChatbotIcon} from './components.jsx';
import './styles/welcome.css';
import './styles/kiosk-preview.css';
const areas=['A','B','C','D'];
export default function ParkingWelcome({onStart,showPromo=false}){
 const helpDialog=useRef(null);
 const [charging,setCharging]=useState(false);
 const [theme,setTheme]=useState(()=>{
  try{return localStorage.getItem('parkon-react-theme')==='dark'?'dark':'light'}
  catch{return 'light'}
 });
 useEffect(()=>{document.documentElement.dataset.theme=theme},[theme]);
 function changeTheme(next){
  setTheme(next);
  try{localStorage.setItem('parkon-react-theme',next)}catch{/* The current screen can still change theme. */}
 }
 const preview=new URLSearchParams(window.location.search).get('preview');
 const kioskPreview=preview==='kiosk-a'||preview==='kiosk-b';
 const showLotChoice=preview==='list'||preview==='cards';
 const [selected,setSelected]=useState(()=>{const id=new URLSearchParams(window.location.search).get('lot');return areas.includes(id)?id:'A'});
 const [language,setLanguage]=useState(getLanguage);
 const t={...welcomeText[language],...welcomeHubText[language]};
 function changeLanguage(value){setLanguage(value);try{localStorage.setItem('parkon-welcome-language',value)}catch{/* Switching also works without storage. */}}
 return <main className="kiosk-welcome" data-preview={preview} data-mobile-start={showPromo||undefined} lang={language}>
  {kioskPreview ? <section className="kiosk-preview">
   <header className="kiosk-preview-header">
    <a className="kiosk-brand" href="/" aria-label={t.home}><BrandWordmark/></a>
    <nav className="kiosk-languages" aria-label={t.language}>{welcomeLanguages.map(([id,label])=><button type="button" key={id} lang={id} aria-pressed={language===id} onClick={()=>changeLanguage(id)}>{label}</button>)}</nav>
   </header>
   <div className="kiosk-preview-main">
    <h1 aria-live="polite">{t.title}</h1>
    <div className="kiosk-preview-actions">
     <button type="button" className="kiosk-preview-pay" onClick={()=>onStart(selected,'fee',language)}><Icon name="wallet"/><span>{t.payment.join(language==='ko'||language==='en'?' ':'')}</span><span aria-hidden="true">→</span></button>
     <button type="button" className="kiosk-preview-find" onClick={()=>onStart(selected,'find',language)}><Icon name="car"/>{t.find}</button>
     <button type="button" className="kiosk-preview-find" onClick={()=>onStart(selected,'chat',language)}><ChatbotIcon/>{t.chat}</button>
    </div>
    <button type="button" className="kiosk-preview-help" onClick={()=>helpDialog.current.showModal()}>{t.help}</button>
   </div>
  </section> : <><section className="kiosk-content"><header className="kiosk-header"><div className="kiosk-intro">{showPromo&&!preview?<WelcomePromo className="kiosk-brand" autoOpen previewOnce={new URLSearchParams(window.location.search).get("promo")==="preview"}/>:<a className="kiosk-brand" href="/" aria-label={t.home}><BrandWordmark/></a>}<h1 className="kiosk-service-title" aria-live="polite">{t.title}</h1></div><nav className="kiosk-languages" aria-label={t.language}>{welcomeLanguages.map(([id,label])=><button type="button" key={id} lang={id} aria-pressed={language===id} onClick={()=>changeLanguage(id)}>{label}</button>)}</nav></header>

   {showLotChoice&&<div className="kiosk-lot-choice"><label htmlFor="welcome-lot"><Icon name="pin"/>{t.lot}</label><select id="welcome-lot" value={selected} onChange={e=>setSelected(e.target.value)}>{areas.map((id,i)=><option key={id} value={id}>{t.lots[i]}</option>)}</select></div>}
   <div className="kiosk-help-toolbar">
    <div className="kiosk-theme-toggle" role="group" aria-label={({ko:'화면 밝기',en:'Appearance','zh-CN':'外观',ja:'画面の明るさ'})[language]}>
     {['light','dark'].map(mode=><button type="button" key={mode} aria-pressed={theme===mode}
      aria-label={({ko:mode==='light'?'밝게':'어둡게',en:mode==='light'?'Light':'Dark','zh-CN':mode==='light'?'浅色':'深色',ja:mode==='light'?'ライト':'ダーク'})[language]}
      onClick={()=>changeTheme(mode)}>
      <svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor">
       {mode==='light'?<><circle cx="12" cy="12" r="5"/><path d="M12 1v3m0 16v3M1 12h3m16 0h3M4.2 4.2l2.1 2.1m11.4 11.4 2.1 2.1M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" fill="none" stroke="currentColor" strokeWidth="2.2"/></>:<path d="M20.8 15.6A9.5 9.5 0 0 1 8.4 3.2a9.5 9.5 0 1 0 12.4 12.4Z"/>}
      </svg>
     </button>)}
    </div>
    <button type="button" className="kiosk-help-icon" aria-label={t.help} title={t.help} onClick={()=>helpDialog.current.showModal()}><Icon name="help-bubble"/><span>{t.help}</span></button>
   </div>
   <div className="kiosk-services"><button className="kiosk-payment" onClick={()=>onStart(selected,'fee',language)}><Icon name="wallet"/><span className="kiosk-payment-title">{t.payment.map(line=><span key={line}>{line}</span>)}</span><span className="kiosk-payment-note">{t.paymentNote}</span><span className="kiosk-payment-arrow" aria-hidden="true"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12h17m-6-6 6 6-6 6"/></svg></span></button><button className="kiosk-secondary" onClick={()=>onStart(selected,'find',language)}><span className="welcome-service-icon welcome-car-icon"><Icon name="car"/></span><span>{showPromo&&language==="ko"?<span className="welcome-find-label">내 차 찾기</span>:t.find}</span><b aria-hidden="true">›</b></button><button className="kiosk-secondary" onClick={()=>onStart(selected,'chat',language)}><span className="welcome-service-icon welcome-chat-icon"><ChatbotIcon/></span><span>{t.chat}</span><b aria-hidden="true">›</b></button><button className="kiosk-secondary" onClick={()=>setCharging(true)}><span className="welcome-service-icon"><ChargingIcon className="icon welcome-charge-glyph" viewBox="6.5 5.5 32 32"/></span><span>{({ko:"충전 현황",en:"EV charging", "zh-CN":"充电状态",ja:"充電状況"})[language]}</span><b aria-hidden="true">›</b></button></div>
   <a className="kiosk-mobile" href={showPromo?`/?app=0&lang=${language}`:`/?app=1&lang=${language}&promo=1`}>
    {showPromo?translate(language,'웹 화면으로 보기'):t.mobile}<span aria-hidden="true"> ↗</span>
   </a>
  </section></>}
  {charging&&<ChargingStatus language={language} initialLot={showLotChoice?selected:undefined} onClose={()=>setCharging(false)}/>}
  <dialog className="kiosk-help" ref={helpDialog} aria-labelledby="welcome-help-title">
   <h2 id="welcome-help-title">{t.help}</h2>
   <ParkingHelp language={language}/>
   <form method="dialog"><button autoFocus>{t.close}</button></form>
  </dialog>
 </main>
}
