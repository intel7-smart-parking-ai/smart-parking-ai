import { useEffect, useState } from 'react';

export default function App() {
  const [server, setServer] = useState('확인 중');
  useEffect(() => {
    fetch('/api/v1/health')
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(data => setServer(data.status === 'ok' ? '연결됨' : '확인 필요'))
      .catch(() => setServer('서버 미연결'));
  }, []);
  return (
    <main>
      <p className="eyebrow">AI SMART PARKING / INITIAL SCAFFOLD</p>
      <h1>AI 스마트 주차장 통합관제 시스템</h1>
      <p>사용자 웹과 관리자 관제 UI는 담당자가 구현합니다.</p>
      <section><strong>백엔드 연결 상태</strong><span>{server}</span></section>
      <small>기능 화면 및 실제 데이터 연결은 아직 구현되지 않았습니다.</small>
    </main>
  );
}
