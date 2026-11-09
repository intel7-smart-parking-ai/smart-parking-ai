const labels={
 ko:{available:'충전 대기',connected:'연결 대기',charging:'충전 중',completed:'충전 완료',not_connected:'충전기 미연결',unknown:'충전 상태 확인 불가'},
 en:{available:'Available',connected:'Connected',charging:'Charging',completed:'Completed',not_connected:'Disconnected',unknown:'Status unknown'},
 'zh-CN':{available:'待充电',connected:'已连接',charging:'充电中',completed:'已完成',not_connected:'未连接',unknown:'状态未知'},
 ja:{available:'充電待機',connected:'接続待機',charging:'充電中',completed:'充電完了',not_connected:'未接続',unknown:'状態不明'}
};
export function chargingText(charging,language='ko'){
 const text=(labels[language]||labels.ko)[charging?.status]||(labels[language]||labels.ko).unknown;
 const power=Number.isFinite(charging?.power_kw)?` · ${charging.power_kw} kW`:'';
 const source=charging?.is_mock?({ko:'시연',en:'Demo','zh-CN':'演示',ja:'デモ'}[language]||'Demo'):'';
 return text+power+(source?` · ${source}`:'');
}

export function remainingText(charging,language='ko'){
 const seconds=charging?.estimated_remaining_seconds;
 if(!Number.isFinite(seconds))return ({ko:'남은 충전시간 정보 없음',en:'Remaining time unavailable','zh-CN':'暂无剩余时间','ja':'残り時間不明'})[language]||'Remaining time unavailable';
 if(seconds===0)return ({ko:'충전 완료',en:'Charging completed','zh-CN':'充电完成',ja:'充電完了'})[language]||'Charging completed';
 const minutes=Math.floor(seconds/60),rest=seconds%60;
 return ({ko:`완료까지 약 ${minutes}분 ${rest}초 · 시연 예상`,en:`About ${minutes}m ${rest}s remaining · Demo estimate`,'zh-CN':`约剩余 ${minutes}分 ${rest}秒 · 演示估计`,ja:`完了まで約${minutes}分${rest}秒 · デモ予測`})[language]||`${minutes}m ${rest}s`;
}
