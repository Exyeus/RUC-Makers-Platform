const bindings = new WeakMap();

function bindPlayer(player) {
  if (bindings.has(player)) return;
  const video = player.querySelector('video');
  const status = player.querySelector('[data-media-status]');
  const button = player.querySelector('[data-media-load]');
  if (!video || !status || !button) return;
  let loading = false;
  let lastTime = 0;
  let restoring = false;
  let disposed = false;
  let controller;
  const rememberTime = () => { if (!restoring) lastTime = video.currentTime; };
  async function load() {
    if (loading || disposed) return;
    loading = true;
    controller = new AbortController();
    button.disabled = true;
    status.textContent = '正在获取材料播放权限…';
    try {
      const response = await fetch(player.dataset.sourceUrl, {
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      });
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('permission');
      const source = await response.json();
      if (disposed) return;
      restoring = true;
      video.src = source.url;
      video.load();
      status.textContent = '材料已加载，可播放、暂停或拖动进度。';
    } catch {
      if (!disposed) status.textContent = '暂时无法加载。请检查网络与登录状态后重试；仍失败可报告播放异常，当前评分不会自动变为0。';
    } finally {
      loading = false;
      if (!disposed) button.disabled = false;
    }
  }
  const restoreTime = () => {
    if (lastTime > 0 && Number.isFinite(video.duration)) video.currentTime = Math.min(lastTime, video.duration);
    restoring = false;
  };
  const showError = () => {
    status.textContent = '播放中断或链接已过期。点击“加载 / 恢复播放”重新获取权限并恢复位置，或报告播放异常。';
  };
  function dispose(event) {
    event?.preventDefault();
    if (disposed) return;
    disposed = true;
    controller?.abort();
    video.removeEventListener('timeupdate', rememberTime);
    video.removeEventListener('loadedmetadata', restoreTime);
    video.removeEventListener('error', showError);
    button.removeEventListener('click', load);
    player.removeEventListener('hackathon:media-dispose', dispose);
    video.pause();
    video.removeAttribute('src');
    video.load();
    bindings.delete(player);
  }
  video.addEventListener('timeupdate', rememberTime);
  video.addEventListener('loadedmetadata', restoreTime);
  video.addEventListener('error', showError);
  button.addEventListener('click', load);
  player.addEventListener('hackathon:media-dispose', dispose);
  bindings.set(player, dispose);
}

function playersIn(node) {
  return [
    ...(node.matches?.('[data-media-player]') ? [node] : []),
    ...(node.querySelectorAll?.('[data-media-player]') || []),
  ];
}

for (const player of playersIn(document)) bindPlayer(player);
// upload.js adds new materials after save/status refresh; this module is loaded once through versioned_static.
new MutationObserver(records => {
  for (const record of records) {
    for (const node of record.removedNodes) {
      if (!node.isConnected) for (const player of playersIn(node)) bindings.get(player)?.();
    }
    for (const node of record.addedNodes) for (const player of playersIn(node)) bindPlayer(player);
  }
}).observe(document.documentElement, { childList: true, subtree: true });
