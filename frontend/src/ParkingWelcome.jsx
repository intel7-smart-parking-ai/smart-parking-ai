import {useRef,useState} from 'react';
import {welcomeLanguages,welcomeText,welcomeHubText} from './welcomeLocales.js';
import {Icon} from './components.jsx';
import './styles/welcome.css';
import './styles/kiosk-preview.css';
const areas=['A','B','C','D'];
export default function ParkingWelcome({onStart}){
 const helpDialog=useRef(null);
 const preview=new URLSearchParams(window.location.search).get('preview');
 const kioskPreview=preview==='kiosk-a'||preview==='kiosk-b';
 const showLotChoice=preview==='list'||preview==='cards';
 const [selected,setSelected]=useState(()=>{const id=new URLSearchParams(window.location.search).get('lot');return areas.includes(id)?id:'A'});
 const [language,setLanguage]=useState(()=>{try{const saved=localStorage.getItem('parkon-welcome-language');return Object.hasOwn(welcomeText,saved)?saved:'ko'}catch{return 'ko'}});
 const t={...welcomeText[language],...welcomeHubText[language]};
 function changeLanguage(value){setLanguage(value);try{localStorage.setItem('parkon-welcome-language',value)}catch{/* Switching also works without storage. */}}
 return <main className="kiosk-welcome" data-preview={preview} lang={language}>
  {kioskPreview ? <section className="kiosk-preview">
   <header className="kiosk-preview-header">
    <a className="kiosk-brand" href="/" aria-label={t.home}>PARK<span>:ON</span></a>
    <nav className="kiosk-languages" aria-label={t.language}>{welcomeLanguages.map(([id,label])=><button type="button" key={id} lang={id} aria-pressed={language===id} onClick={()=>changeLanguage(id)}>{label}</button>)}</nav>
   </header>
   <div className="kiosk-preview-main">
    <h1 aria-live="polite">{t.title}</h1>
    <div className="kiosk-preview-actions">
     <button type="button" className="kiosk-preview-pay" onClick={()=>onStart(selected,'fee')}><Icon name="wallet"/><span>{t.payment.join(language==='ko'||language==='en'?' ':'')}</span><span aria-hidden="true">→</span></button>
     <button type="button" className="kiosk-preview-find" onClick={()=>onStart(selected,'find')}><Icon name="car"/>{t.find}</button>
    </div>
    <button type="button" className="kiosk-preview-help" onClick={()=>helpDialog.current.showModal()}>{t.help}</button>
   </div>
  </section> : <><section className="kiosk-content"><header className="kiosk-header"><div className="kiosk-intro"><a className="kiosk-brand" href="/" aria-label={t.home}>PARK<span>:ON</span></a><h1 className="kiosk-service-title" aria-live="polite">{t.title}</h1></div><nav className="kiosk-languages" aria-label={t.language}>{welcomeLanguages.map(([id,label])=><button type="button" key={id} lang={id} aria-pressed={language===id} onClick={()=>changeLanguage(id)}>{label}</button>)}</nav></header>

   {showLotChoice&&<div className="kiosk-lot-choice"><label htmlFor="welcome-lot"><Icon name="pin"/>{t.lot}</label><select id="welcome-lot" value={selected} onChange={e=>setSelected(e.target.value)}>{areas.map((id,i)=><option key={id} value={id}>{t.lots[i]}</option>)}</select></div>}
   <div className="kiosk-services"><button className="kiosk-payment" onClick={()=>onStart(selected,'fee')}><Icon name="wallet"/><span className="kiosk-payment-title">{t.payment.map(line=><span key={line}>{line}</span>)}</span><span className="kiosk-payment-note">{t.paymentNote}</span><span className="kiosk-payment-arrow" aria-hidden="true">→</span></button><button className="kiosk-secondary" onClick={()=>onStart(selected,'find')}><Icon name="car"/><span>{t.find}</span><b aria-hidden="true">›</b></button><button className="kiosk-secondary" onClick={()=>helpDialog.current.showModal()}><Icon name="info"/><span>{t.help}</span><b aria-hidden="true">›</b></button></div>
   <a className="kiosk-mobile" href="/?app=1">{t.mobile}<span aria-hidden="true"> ↗</span></a>
  </section><footer className="kiosk-footer"><span>PARK:ON</span><span>{t.demo}</span></footer></>}
  <dialog className="kiosk-help" ref={helpDialog} aria-labelledby="welcome-help-title"><h2 id="welcome-help-title">{t.help}</h2><ol>{(showLotChoice?t.helpSteps:t.helpSteps.slice(1)).map(step=><li key={step}>{step}</li>)}</ol><p>{t.demo}</p><form method="dialog"><button>{t.close}</button></form></dialog>
 </main>
}
