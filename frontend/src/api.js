import {normalizeLot} from './uiState.js';
// Build-time selection; URL parameters cannot switch to another backend.
export const dataSource = import.meta.env?.VITE_DATA_SOURCE || 'demo';
if (!['demo', 'team'].includes(dataSource)) throw new Error('VITE_DATA_SOURCE는 demo 또는 team이어야 합니다.');
export const pollInterval = dataSource === 'team' ? 2500 : 3000;
const isAdmin = () => typeof window !== 'undefined' && window.location.pathname.startsWith('/admin/');
export function listItems(result) {
  if (Array.isArray(result)) return result;
  if (!Array.isArray(result?.items) || result.next_cursor) throw new Error('목록 응답 형식을 확인해주세요. 페이지가 누락되었습니다.');
  return result.items;
}
export function feeView(fee) {
  if (!Number.isInteger(fee?.amount_krw) || fee.amount_krw < 0) throw new Error('올바른 요금 응답이 없습니다.');
  return {...fee, policy: fee.rate_policy || fee.policy, base_amount_krw: fee.fee_breakdown?.base_fee_krw ?? fee.base_amount_krw,
    extra_units: fee.fee_breakdown?.extra_units ?? fee.extra_units, extra_amount_krw: fee.fee_breakdown?.extra_fee_krw ?? fee.extra_amount_krw,
    cap_discount_krw: fee.fee_breakdown?.cap_discount_krw ?? fee.cap_discount_krw,
    payment_status: fee.payment_status || fee.payment?.status || 'unpaid'};
}
export async function request(path, options = {}, source = dataSource) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const prefix = isAdmin() ? (source === 'demo' ? '/admin-api/demo/v1' : '/admin-api/team/v1') : (source === 'demo' ? '/demo-api/v1' : '/api/v1');
    const response = await fetch(prefix + path, {method: options.method || 'GET', cache:'no-store', headers:{'Content-Type':'application/json'}, signal:controller.signal, ...(options.body ? {body:JSON.stringify(options.body)} : {})});
    if (response.status === 401 && isAdmin()) {window.location.assign('/admin/login'); throw new Error('관리자 로그인이 필요합니다.');}
    let data;
    try {data = await response.json();} catch {data = {};}
    if (!response.ok) {
      let message = data.error?.message || data.message || (typeof data.detail === 'string' ? data.detail : null);
      // The receiver can replace route details with HTTP_ERROR. Explain this
      // lookup's status locally while preserving specific server error codes.
      if (path.split('?')[0] === '/vehicle-search' &&
          (!data.error?.code || ['HTTP_ERROR', 'VALIDATION_ERROR'].includes(data.error.code))) {
        const guidance = {
          400: '차량번호 뒤 4자리를 정확히 입력해주세요.',
          422: '차량번호 뒤 4자리를 정확히 입력해주세요.',
          404: '일치하는 차량을 찾지 못했습니다. 번호와 현재 주차 상태를 확인해주세요.',
          503: '차량 조회 서버가 일시적으로 응답하지 않습니다. 잠시 후 다시 조회해주세요.',
        };
        message = guidance[response.status] || message;
      }
      const error = new Error(message || (response.status === 404 || response.status === 501 ? '서버에 아직 연결되지 않은 기능입니다.' : 'API 요청을 처리하지 못했습니다.'));
      error.code = data.error?.code || data.error_code || data.code; error.status = response.status; throw error;
    }
    return data;
  } catch (error) {if (error.name === 'AbortError') throw new Error('응답 시간이 초과되었습니다.'); throw error;}
  finally {clearTimeout(timeout);}
}
export async function api(path, options) {
  const result = await request(path, options);
  if (/^\/(rate-policies|mock-payments|sessions|alerts)(\?|$)/.test(path) && (!options?.method || options.method === 'GET')) return listItems(result);
  if (/^\/sessions\/[^/]+\/fee$/.test(path)) return feeView(result);
  return result;
}
export async function loadLots(source = dataSource) {
  const visibleDemo = source === 'team' && typeof document !== 'undefined' && !document.hidden;
  const result = await request(visibleDemo ? '/parking-lots?visible_demo=1' : '/parking-lots', {}, source);
  if (source === 'demo') return listItems(result).map(lot=>normalizeLot(lot,lot.spaces||[],result.as_of));
  return Promise.all(listItems(result).map(async lot => {
    const response = await request(`/parking-lots/${encodeURIComponent(lot.lot_id)}/spaces`, {}, source);
    return normalizeLot(lot,listItems(response),response.as_of);
  }));
}
export async function lookupVehicle(value, kind = 'sticker', source = dataSource) {
  const input = value.trim();
  if (kind === 'plate') {
    if (!/^\d{4}$/.test(input)) throw new Error('차량번호 뒤 4자리를 정확히 입력해주세요.');
    const result=await request('/vehicle-search?vehicle_number='+input,{},source);
    const fee=result.fee||(result.session_id?await request('/sessions/'+encodeURIComponent(result.session_id)+'/fee',{},source):null);
    return {...result,fee:fee?feeView(fee):null};
  }
  if (/^\d$/.test(input)) throw new Error('스티커 번호는 두 자리로 입력해주세요. (10~99)');
  const number=input;
  if (!/^[1-9][0-9]$/.test(number)) throw new Error('스티커 번호 10~99를 입력해주세요. 00~09는 사용하지 않습니다.');
  let result;
  try {
    result=await request('/vehicles/'+number+'/location',{},source);
  } catch(error) {
    if(error.status===404)throw new Error('스티커 '+number+'번의 현재 주차 위치가 확인되지 않습니다. 입차·카메라 인식 상태를 확인해 주세요. 가상 차량은 차량번호로 찾기에서 뒤 4자리를 입력하세요.');
    throw error;
  }
  if (!result.session_id) return {...result,lookup_type:'sticker',fee:null,fee_notice:'요금 정보 없음 · 주차 세션이 아직 없습니다.'};
  try {
    const fee=await request('/sessions/'+encodeURIComponent(result.session_id)+'/fee',{},source);
    return {...result,lookup_type:'sticker',entry_at:fee.entry_at||result.entry_at,fee:feeView(fee)};
  } catch(error) {
    if (error.status===404 || error.status===501) return {...result,lookup_type:'sticker',fee:null,fee_notice:'요금 정보 없음 · 서버 요금 API 연결 대기'};
    throw error;
  }
}
