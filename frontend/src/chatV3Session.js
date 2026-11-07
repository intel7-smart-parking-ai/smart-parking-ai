// Isolated preview transport: no production chat state or automatic retries.
export async function previewRequest(path, {body, fetchImpl = globalThis.fetch, timeoutMs = 20000} = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl('/chat-v3-api/v1/chat' + path, {
      method: body ? 'POST' : 'GET', cache: 'no-store', signal: controller.signal,
      headers: {'Content-Type': 'application/json'}, ...(body ? {body: JSON.stringify(body)} : {}),
    });
    let data;
    try {data = await response.json();} catch {throw new Error('서버 응답을 읽지 못했습니다. 같은 요청을 다시 확인할 수 있습니다.');}
    if (!response.ok) {
      const error = new Error(data.error?.message || '서버 요청을 완료하지 못했습니다.');
      error.code = data.error?.code; error.status = response.status;
      throw error;
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('응답 대기 시간이 지났습니다. 서버 작업은 진행 중일 수 있습니다.');
    throw error;
  } finally {clearTimeout(timer);}
}

const resetCodes = new Set(['SESSION_EXPIRED', 'RUNTIME_CHANGED', 'SESSION_LIMIT', 'SESSION_UNKNOWN']);
export function createV3Session({request = previewRequest, makeId = () => crypto.randomUUID()} = {}) {
  const listeners = new Set();
  let state = {config: null, rows: [], draft: '', busy: false, error: '', pending: null, resetRequired: false};
  let generation = 0;
  const emit = changes => {state = {...state, ...changes}; for (const listener of listeners) listener();};
  async function start() {
    if (state.busy) return;
    const epoch = ++generation;
    emit({busy: true, error: ''});
    try {
      const config = await request('/config');
      if (!config || typeof config.session_id !== 'string' || typeof config.runtime_id !== 'string' ||
          !['fixture', 'openrouter'].includes(config.model_mode) || !config.supported_languages?.includes('ko')) {
        throw new Error('시험 서버의 설정 형식을 확인해주세요.');
      }
      if (epoch === generation) emit({config, rows: [], draft: '', pending: null, resetRequired: false});
    } catch (error) {if (epoch === generation) emit({error: error.message});}
    finally {if (epoch === generation) emit({busy: false});}
  }
  async function execute(body) {
    const epoch = generation;
    emit({busy: true, error: ''});
    try {
      const result = await request('', {body});
      if (epoch !== generation) return;
      if (result.runtime_id !== body.runtime_id) {
        const error = new Error('시험 서버가 다시 시작됐습니다. 새 대화를 시작해주세요.');
        error.code = 'RUNTIME_CHANGED'; throw error;
      }
      if (typeof result.text !== 'string' || !Array.isArray(result.messages) || result.messages[0]?.text !== result.text) {
        throw new Error('응답 형식을 확인할 수 없습니다. 같은 요청을 다시 확인해주세요.');
      }
      emit({rows: [...state.rows, {who: 'assistant', text: result.text, result}], pending: null});
    } catch (error) {
      if (epoch === generation) emit({error: error.message, resetRequired: resetCodes.has(error.code)});
    } finally {if (epoch === generation) emit({busy: false});}
  }
  return {
    subscribe(listener) {listeners.add(listener); return () => listeners.delete(listener);},
    getSnapshot: () => state,
    edit(draft) {if (!state.busy && !state.pending) emit({draft});},
    start,
    async send() {
      if (state.busy || state.pending || state.resetRequired || !state.config) return;
      const text = state.draft.trim();
      if (!text || [...text].length > 200) {emit({error: '질문은 1~200자로 입력해주세요.'}); return;}
      const body = {session_id: state.config.session_id, runtime_id: state.config.runtime_id, request_id: makeId(), text, language: 'ko'};
      emit({pending: body, draft: '', rows: [...state.rows, {who: 'user', text}]});
      await execute(body);
    },
    async retry() {
      if (state.pending && !state.busy && !state.resetRequired) await execute(state.pending);
    },
  };
}
