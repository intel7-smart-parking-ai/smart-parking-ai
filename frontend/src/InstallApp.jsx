import {useEffect, useRef, useState} from 'react';

export default function InstallApp() {
  const [prompt, setPrompt] = useState(null);
  const [installed, setInstalled] = useState(() => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true);
  const [showHelp, setShowHelp] = useState(false);
  const [update, setUpdate] = useState(null);
  const helpRef = useRef(null);
  useEffect(() => {
    const ready = event => {event.preventDefault(); setPrompt(event);};
    const done = () => {setInstalled(true); setPrompt(null);};
    const newer = event => setUpdate(event.detail);
    window.addEventListener('beforeinstallprompt', ready);
    window.addEventListener('appinstalled', done);
    window.addEventListener('parkon-update', newer);
    return () => {window.removeEventListener('beforeinstallprompt', ready); window.removeEventListener('appinstalled', done); window.removeEventListener('parkon-update', newer);};
  }, []);
  useEffect(() => {if (showHelp) helpRef.current?.showModal();}, [showHelp]);
  async function install() {
    if (!prompt) {setShowHelp(true); return;}
    await prompt.prompt();
    await prompt.userChoice;
    setPrompt(null);
  }
  function applyUpdate() {
    navigator.serviceWorker.addEventListener('controllerchange', () => window.location.reload(), {once: true});
    update.waiting?.postMessage('SKIP_WAITING');
  }
  return <>
    {update ? <button className="app-install-button" onClick={applyUpdate}>새 버전 적용</button> : !installed && <button className="app-install-button" onClick={install}>앱 설치</button>}
    {showHelp && <dialog className="install-help" ref={helpRef} onClose={() => setShowHelp(false)}><button className="install-close" onClick={() => helpRef.current.close()} aria-label="설치 안내 닫기">×</button><img src="/icons/icon-192.png" alt=""/><h2>PARK:ON을 홈 화면에</h2><p>앱처럼 열고 주차장 빈자리를 확인하세요.</p><ol><li><b>Android · Chrome</b><br/>브라우저 메뉴 → 앱 설치 또는 홈 화면에 추가</li><li><b>iPhone · Safari</b><br/>공유 버튼 → 홈 화면에 추가 → 추가</li></ol>{!window.isSecureContext && <p className="install-requirement">이 주소에서는 설치가 지원되지 않습니다. HTTPS로 연결된 앱 주소에서 열어주세요.</p>}<p className="install-requirement">이 PC의 127.0.0.1 주소는 휴대폰에서 사용할 수 없습니다. 휴대폰용 서버 주소가 필요합니다.</p><button className="button primary full" onClick={() => helpRef.current.close()}>확인</button></dialog>}
  </>;
}
