import BrandWordmark from './BrandWordmark.jsx';
import {Icon} from './components.jsx';
import {t} from './serviceLocale.js';
import './styles/connection-loading.css';

export default function ConnectionLoading({error}) {
 return <main className="connection-loading" role={error?'alert':'status'} aria-live="polite" aria-busy={!error}>
  <div className="connection-loading-content">
   <div className="connection-loading-mark" aria-hidden="true"><span>P</span><Icon name="car"/></div>
   <BrandWordmark showCar={false}/>
   <h1>{error || t('서버에 연결하고 있어요')}</h1>
   {error
    ? <button type="button" className="button primary" onClick={()=>location.reload()}>{t('다시 연결')}</button>
    : <><p>{t('잠시만 기다려 주세요')}</p><div className="connection-loading-dots" aria-hidden="true"><i/><i/><i/></div></>}
  </div>
 </main>;
}
