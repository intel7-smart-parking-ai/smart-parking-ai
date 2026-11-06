// One session and immutable request envelope for both typed and transcribed input.
export function createChatSession({request, makeId}) {
  let state = {draft: '', rows: [], view: null, busy: false, error: '', retry: null, restartRequired: false, actionEpoch: 0};
  let sessionId = makeId(), runtimeId = null, version = -1, sequence = 0, generation = 0;
  let polling = false, cursor = 0, alertVersion = 0, alertEpoch = 0, alertKey = '';
  const listeners = new Set(), events = new Set();
  const emit = patch => {state = {...state, ...patch}; listeners.forEach(fn => fn());};
  const replyText = r => typeof r?.text === 'string' ? r.text : (r?.messages || []).map(m => typeof m === 'string' ? m : m.text).filter(Boolean).join('\n');
  function accept(result) {
    if (runtimeId && result.runtime_id && runtimeId !== result.runtime_id) {
      emit({restartRequired: true, retry: null, actionEpoch: state.actionEpoch + 1, error: '서버가 다시 시작됐어요. 새 대화를 시작해 주세요.'});
      return false;
    }
    if (Number.isFinite(result.state_version) && result.state_version < version) return false;
    runtimeId = result.runtime_id || runtimeId;
    version = Number.isFinite(result.state_version) ? result.state_version : version;
    alertVersion = Math.max(alertVersion, result.alert_version || 0);
    return true;
  }
  function addReply(result) {
    const epoch = state.actionEpoch + 1;
    emit({view: result, actionEpoch: epoch, rows: [...state.rows, {who: 'assistant', text: replyText(result), result, epoch}]});
  }
  async function transmit(task) {
    const turn = ++sequence, life = generation, safetyAtStart = alertEpoch;
    emit({busy: true, error: '', retry: null});
    try {
      const result = await request('/chat', {method: 'POST', body: task.body}, 'team');
      if (life !== generation || turn !== sequence || safetyAtStart !== alertEpoch) return;
      if (!accept(result)) return;
      if (result.meta?.busy) {
        emit({error: replyText(result) || '응답을 준비 중이에요. 같은 요청을 다시 시도해 주세요.', retry: task,
          ...(result.safety ? {view: {...state.view, safety: result.safety, banner: result.banner, chips: []}} : {})});
        return;
      }
      if (!replyText(result) && !result.card && !result.safety) throw new Error('응답을 확인하지 못했어요. 같은 요청을 다시 확인해 주세요.');
      addReply(result);
    } catch (error) {
      if (life !== generation || turn !== sequence || safetyAtStart !== alertEpoch) return;
      const retryable = !error.status || error.status === 409 || error.status === 429 || error.status >= 500;
      emit({error: error.message || '연결을 확인한 뒤 같은 요청을 다시 시도해 주세요.', retry: retryable ? task : null});
    } finally {
      if (life === generation && turn === sequence) emit({busy: false});
    }
  }
  function submit(body, label) {
    if (state.busy || state.restartRequired) return Promise.resolve();
    const task = {body: Object.freeze({session_id: sessionId, ...body, request_id: makeId(), ...(runtimeId ? {runtime_id: runtimeId} : {})}), label};
    emit({rows: [...state.rows, {who: 'user', text: label}], view: state.view ? {...state.view, chips: []} : null, actionEpoch: state.actionEpoch + 1});
    return transmit(task);
  }
  const controller = {
    getSnapshot: () => state,
    subscribe(fn) {listeners.add(fn); return () => listeners.delete(fn);},
    edit(value) {emit({draft: value});},
    async send() {
      if (state.busy || state.restartRequired || !state.draft.trim()) return;
      if ([...state.draft].length > 200) {emit({error: '질문은 200자 이내로 줄여 주세요. 입력 내용은 그대로 남겨 두었어요.'}); return;}
      const text = state.draft; // Clear on submission, never when a response arrives.
      emit({draft: ''});
      return submit({text}, text);
    },
    choose(chip, epoch) {
      if (epoch !== state.actionEpoch || state.view?.safety || Number(state.view?.banner?.level) >= 2) {
        // Safety actions remain usable; only stale confirmation actions are blocked.
        if (epoch !== state.actionEpoch || chip?.confirmation_token || /^confirm_/.test(chip?.id || '')) return Promise.resolve();
      }
      if (!chip?.id) return Promise.resolve();
      return submit({chip_id: chip.id, ...(chip.confirmation_token ? {confirmation_token: chip.confirmation_token} : {})}, chip.label || chip.id);
    },
    retry() {if (state.retry && !state.busy && !state.restartRequired) return transmit(state.retry); return Promise.resolve();},
    invalidateAlerts(alerts = []) {
      const key = alerts.filter(a => Number(a.level) >= 2 && a.status !== 'resolved').map(a => `${a.alert_id || a.lot_id}:${a.level}:${a.observed_at || a.detected_at || ''}`).sort().join('|');
      if (key === alertKey) return;
      alertKey = key;
      if (!key) return; // Do not resurrect old confirmation buttons after a clear.
      alertEpoch++;
      const guidance = '대피 안내가 발생했습니다. 결제를 중단하고 현장 안전 안내를 따라 주세요.';
      emit({actionEpoch: state.actionEpoch + 1, retry: null, view: {...state.view, chips: [], banner: {level: 2, text: guidance}, safety: {title: '안전 안내', guidance}}});
    },
    async poll() {
      if (polling || state.busy || state.restartRequired || !runtimeId) return;
      polling = true;
      const turn = sequence, life = generation, safetyAtStart = alertEpoch;
      try {
        const query = new URLSearchParams({session_id: sessionId, runtime_id: runtimeId, cursor: String(cursor), since: String(alertVersion)});
        const result = await request('/chat/updates?' + query, {}, 'team');
        if (life !== generation || turn !== sequence || safetyAtStart !== alertEpoch || !accept(result)) return;
        cursor = result.cursor ?? cursor;
        const messages = (result.messages || []).filter(m => !events.has(m.event_id));
        for (const message of messages) {events.add(message.event_id); addReply({...message, runtime_id: result.runtime_id, state_version: result.state_version});}
        const elevated = Number(result.banner?.level) >= 2;
        const epoch = state.actionEpoch + (elevated || result.resync_required ? 1 : 0);
        // An alert poll cannot erase a payment-block safety card when alerts are unknown/zero.
        const cleared = Number(state.view?.banner?.level) >= 2 && !elevated && result.alert_version > (state.view?.alert_version || 0);
        const safety = elevated ? (messages.at(-1)?.safety || {title: '안전 안내', guidance: result.banner.text}) : cleared ? null : state.view?.safety;
        emit({actionEpoch: epoch, retry: elevated || result.resync_required ? null : state.retry,
          view: {...state.view, alert_version: result.alert_version, vehicle_context: result.vehicle_context,
            banner: safety && !elevated ? state.view?.banner : result.banner, safety,
            ...(Array.isArray(result.chips) ? {chips: result.chips} : result.resync_required ? {chips: []} : {})}});
      } catch { /* A failed background read must not clear known safety state. */ }
      finally {polling = false;}
    },
    reset() {
      generation++; sequence++; sessionId = makeId(); runtimeId = null; version = -1; cursor = 0; alertVersion = 0; events.clear();
      emit({rows: [], view: null, busy: false, error: '', retry: null, restartRequired: false, actionEpoch: state.actionEpoch + 1});
      if (alertKey) {const key = alertKey; alertKey = ''; controller.invalidateAlerts([{alert_id: key, level: 2}]);}
    },
    cancelPending() {generation++; sequence++;},
  };
  return controller;
}
