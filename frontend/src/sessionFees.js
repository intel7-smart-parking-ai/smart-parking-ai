import {useEffect, useRef, useState} from 'react';
import {request} from './api.js';

// 관리자 화면의 차량별 요금 조회 (11/10 부하 점검).
// 예전: 주차된 차마다 /fee 를 15초마다 + 세션이 바뀔 때마다 전부 다시 조회 → C·D 40~60건 묶음.
// 지금: 새로 들어온 차만 바로 조회, 나머지는 30초마다 한 번, 탭이 안 보이면 쉬기. 요금은 10분 단위라 체감 차이 없음.
export const SESSION_FEE_REFRESH_MS = 30000;

export function useSessionFees(sessions, {enabled = true, fetchFee = id => request('/sessions/' + encodeURIComponent(id) + '/fee')} = {}) {
  const [fees, setFees] = useState({});
  const feesRef = useRef(fees);
  feesRef.current = fees;
  const ids = enabled ? sessions.split(',').filter(Boolean) : [];
  const key = ids.join(',');

  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;
    const current = key.split(',').filter(Boolean);
    async function load(targets) {
      const entries = await Promise.all(targets.map(async id => {
        try {return [id, await fetchFee(id)];} catch {return [id, null];}
      }));
      if (!alive) return;
      setFees(old => {
        const next = {};
        for (const id of current) if (id in old) next[id] = old[id];
        for (const [id, value] of entries) next[id] = value;
        return next;
      });
    }
    // 처음 보는 세션만 즉시 조회하고, 나간 차의 요금은 버린다.
    const fresh = current.filter(id => !(id in feesRef.current));
    if (fresh.length) load(fresh);
    else setFees(old => Object.keys(old).every(id => current.includes(id)) ? old : Object.fromEntries(current.filter(id => id in old).map(id => [id, old[id]])));
    const timer = setInterval(() => {if (typeof document === 'undefined' || !document.hidden) load(current);}, SESSION_FEE_REFRESH_MS);
    return () => {alive = false; clearInterval(timer);};
  }, [key, enabled]); // eslint-disable-line react-hooks/exhaustive-deps

  return fees;
}
