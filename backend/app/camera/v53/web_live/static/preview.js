// Each camera has one outstanding request. A slow reader skips older frames.
function createParkingPreview(camera, canvas, placeholder, turnButton) {
  const context = canvas.getContext('2d');
  const storageKey = 'v53-preview-quarter-turns-' + camera;
  let turns = 0;
  try { turns = Number(localStorage.getItem(storageKey)) & 3; } catch {}
  let active = false, generation = 0, timer, expiry, request;
  let epoch = -1, sequence = -1, bitmap = null;

  function blank(message) {
    clearTimeout(expiry);
    bitmap?.close();
    bitmap = null;
    canvas.hidden = true;
    placeholder.hidden = false;
    placeholder.textContent = message;
    epoch = sequence = -1;
  }

  function draw() {
    if (!bitmap) return;
    const width = turns % 2 ? bitmap.height : bitmap.width;
    const height = turns % 2 ? bitmap.width : bitmap.height;
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    context.setTransform(1, 0, 0, 1, width / 2, height / 2);
    context.rotate(turns * Math.PI / 2);
    context.drawImage(bitmap, -bitmap.width / 2, -bitmap.height / 2);
    placeholder.hidden = true;
    canvas.hidden = false;
  }

  turnButton.onclick = () => {
    turns = (turns + 1) % 4;
    try { localStorage.setItem(storageKey, String(turns)); } catch {}
    draw();
  };

  async function next(run) {
    let retryMs = 65;
    const started = performance.now();
    const requestedAt = Date.now();
    const controller = new AbortController();
    request = controller;
    const timeout = setTimeout(() => controller.abort(), 1500);
    try {
      const response = await fetch('/live/preview/' + camera + '?epoch=' + epoch + '&sequence=' + sequence,
        {signal: controller.signal, cache: 'no-store'});
      if (run !== generation) return;
      if (response.status === 204) {
        if (response.headers.get('X-Preview-State') === 'offline') {
          blank('카메라 영상 수신 대기');
          retryMs = 350;
        }
      } else {
        if (!response.ok) throw new Error('preview');
        const fresh = await createImageBitmap(await response.blob());
        if (run !== generation) { fresh.close(); return; }
        const age = Date.now() - requestedAt;
        if (age < 0 || age >= 1250) {
          fresh.close();
          blank('새 영상 수신 대기');
          return;
        }
        bitmap?.close();
        bitmap = fresh;
        epoch = Number(response.headers.get('X-Frame-Epoch'));
        sequence = Number(response.headers.get('X-Frame-Sequence'));
        draw();
        clearTimeout(expiry);
        expiry = setTimeout(() => blank('새 영상 수신 대기'), 1250-age);
      }
    } catch {
      retryMs = 350;
    } finally {
      clearTimeout(timeout);
      if (run === generation && active)
        timer = setTimeout(() => next(run), Math.max(0, retryMs - (performance.now() - started)));
    }
  }

  return function setRunning(enabled) {
    if (enabled === active) return;
    active = enabled;
    generation++;
    clearTimeout(timer);
    request?.abort();
    blank(enabled ? '카메라 영상 수신 대기' : '카메라 시작 대기');
    if (enabled) next(generation);
  };
}
