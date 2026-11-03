// The user-facing app always uses the final four-lot prototype until official integration.
// Legacy ?source=team links must not reveal the obsolete 3-lot scaffold.
export const dataSource = 'demo';

export async function request(path, options = {}, source = dataSource) {
  if (source === 'team' && (options.method || 'GET') !== 'GET') {
    throw new Error('팀 API는 현재 조회만 연결되어 있습니다.');
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(((typeof window !== 'undefined' && window.location.pathname.startsWith('/admin/')) ? (source === 'demo' ? '/admin-api/demo/v1' : '/admin-api/team/v1') : (source === 'demo' ? '/demo-api/v1' : '/api/v1')) + path, {
      method: options.method || 'GET', cache: 'no-store', headers: {'Content-Type': 'application/json'},
      signal: controller.signal, ...(options.body ? {body: JSON.stringify(options.body)} : {}),
    });
    if (response.status === 401 && (typeof window !== 'undefined' && window.location.pathname.startsWith('/admin/'))) { window.location.assign('/admin/login'); throw new Error('관리자 로그인이 필요합니다.'); }
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(data.error?.message || data.message ||
        (typeof data.detail === 'string' ? data.detail : 'API 요청을 처리하지 못했습니다.'));
      error.code = data.error?.code || data.code;
      throw error;
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('응답 시간이 초과되었습니다.');
    throw error;
  } finally { clearTimeout(timeout); }
}

export const api = (path, options) => request(path, options);

export async function loadLots(source = dataSource) {
  const result = await request('/parking-lots', {}, source);
  if (source === 'demo') return result;
  if (!Array.isArray(result.items) || result.next_cursor) throw new Error('주차장 목록 응답 형식을 확인해주세요.');
  return Promise.all(result.items.map(async lot => {
    const response = await request(`/parking-lots/${encodeURIComponent(lot.lot_id)}/spaces`, {}, source);
    if (!Array.isArray(response.items) || response.next_cursor) throw new Error('주차칸 목록 응답 형식을 확인해주세요.');
    const split = Math.ceil(response.items.length / 2);
    return {...lot, updated_at: response.as_of, spaces: response.items.map((space, index) => ({
      ...space, lot_id: lot.lot_id,
      // Presentation columns only: do not infer physical ROI or dedicated bay types.
      zone: index < split ? 'A' : 'B', space_type: space.space_type || 'unknown',
    }))};
  }));
}

export async function lookupVehicle(value, kind = 'plate') {
  const input = value.trim();
  const number = input;
  if (kind === 'sticker') {
    if (/^0?[0-9]$/.test(number)) throw new Error('해당 스티커 번호의 차량이 없습니다.');
    if (!/^[1-9][0-9]$/.test(number)) throw new Error('스티커 번호 10~99를 입력해주세요.');
  } else if (!/^[0-9]{4}$/.test(number)) throw new Error('차량번호 뒤 4자리를 정확히 입력해주세요.');
  if (dataSource !== 'demo') throw new Error('팀 서버의 검색 API는 연결 준비 중입니다. 목업 시연에서 체험해주세요.');
  return request(kind === 'sticker' ? '/sticker-search?car_id=' + number : '/vehicle-search?vehicle_number=' + number);
}
