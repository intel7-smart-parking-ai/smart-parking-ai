import {t} from './serviceLocale.js';
export default function EntryVehiclePhoto({plate}){
 return <figure className="entry-result-photo"><svg viewBox="390 180 760 600" role="img" aria-label={t("차량 입차 장면 시연용 합성 이미지")} style={{display:'block',width:'100%'}}><image href="/lot-photos/demo-incoming-vehicle.jpg" width="1536" height="1024"/><text x="789" y="590" textAnchor="middle" dominantBaseline="middle" fill="#152338" fontSize="32" fontWeight="800">{plate||t('미확인')}</text></svg><figcaption>{t("입차 사진 예시 · 시연용 합성 이미지")}</figcaption></figure>;
}
