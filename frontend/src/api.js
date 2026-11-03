// Build-time selection; URL parameters cannot switch to another backend.
export const dataSource = import.meta.env?.VITE_DATA_SOURCE || 'demo';
if (!['demo', 'team'].includes(dataSource)) throw new Error('VITE_DATA_SOURCE는 demo 또는 team이어야 합니다.');
export const pollInterval = dataSource === 'team' ? 2500 : 15000;
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
      const error = new Error(data.error?.message || data.message || (typeof data.detail === 'string' ? data.detail : response.status === 404 || response.status === 501 ? '서버에 아직 연결되지 않은 기능입니다.' : 'API 요청을 처리하지 못했습니다.'));
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
  const result = await request('/parking-lots', {}, source);
  if (source === 'demo') return result;
  return Promise.all(listItems(result).map(async lot => {
    const response = await request(`/parking-lots/${encodeURIComponent(lot.lot_id)}/spaces`, {}, source);
    const spaces = listItems(response), split = Math.ceil(spaces.length / 2);
    return {...lot, updated_at:response.as_of, spaces:spaces.map((space,index)=>({...space,lot_id:lot.lot_id,zone:index<split?'A':'B',space_type:space.space_type||'unknown'}))};
  }));
}
export async function lookupVehicle(value, kind = 'sticker', source = dataSource) {
  const input = value.trim();
  if (kind === 'plate') {
    if (source !== 'demo') throw new Error('팀 서버에서는 스티커 번호 01~24로 조회해주세요.');
    if (!/^\d{4}$/.test(input)) throw new Error('차량번호 뒤 4자리를 정확히 입력해주세요.');
    const result=await request('/vehicle-search?vehicle_number='+input,{},source);
    return {...result,fee:result.fee?feeView(result.fee):null};
  }
  const number=input.padStart(2,'0');
  if (!/^(0[1-9]|1[0-9]|2[0-4])$/.test(number)) throw new Error('스티커 번호 01~24를 입력해주세요.');
  const result=await request('/vehicles/'+number+'/location',{},source);
  if (!result.session_id) return {...result,lookup_type:'sticker',fee:null,fee_notice:'요금 정보 없음 · 주차 세션이 아직 없습니다.'};
  try {
    const fee=await request('/sessions/'+encodeURIComponent(result.session_id)+'/fee',{},source);
    return {...result,lookup_type:'sticker',entry_at:fee.entry_at||result.entry_at,fee:feeView(fee)};
  } catch(error) {
    if (error.status===404 || error.status===501) return {...result,lookup_type:'sticker',fee:null,fee_notice:'요금 정보 없음 · 서버 요금 API 연결 대기'};
    throw error;
  }
}
