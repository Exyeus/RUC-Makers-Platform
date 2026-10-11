const form = document.querySelector('[data-upload-form]');
const csrf = document.querySelector('[name=csrfmiddlewaretoken]')?.value;
const projectForm = document.querySelector('[data-project-form]');
const projectStatus = document.querySelector('[data-project-status-url]');
let registration = null;

class UploadError extends Error {
  constructor(message, status, retryAfter = 5) {
    super(message); this.status = status; this.retryAfter = retryAfter;
  }
}

// Throws UploadError for rejected requests; preserves fetch/timeout errors.
async function request(url, options = {}) {
  const response = await fetch(url, {
    ...options, cache: 'no-store',
    headers: { 'X-CSRFToken': csrf, Accept: 'application/json', ...options.headers },
    signal: options.signal || AbortSignal.timeout(60000),
  });
  const retryAfter = Math.max(1, Math.min(60, Number(response.headers.get('Retry-After')) || 5));
  if (!response.headers.get('Content-Type')?.includes('application/json')) {
    throw new UploadError('登录已过期或服务暂时不可用。请重新登录后选择原文件继续。', response.status, retryAfter);
  }
  const data = await response.json();
  if (!response.ok) throw new UploadError(data.error || `请求失败（${response.status}）`, response.status, retryAfter);
  return data;
}

function newId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, x => x.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}

function remember(key, value) {
  try { localStorage.setItem(key, value); }
  catch (error) { console.warn('Could not persist hackathon upload evidence', error); }
}

// Rejects when paused, including while waiting for an upload slot.
function delay(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const stop = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', stop); resolve(); }, milliseconds);
    signal.addEventListener('abort', stop, { once: true });
  });
}

const stateLabels = {
  hashing: '已登记，等待文件哈希', queued: '已登记，等待上传通道', uploading: '上传中',
  complete: '已校验并保存', cancelled: '已取消', expired: '续传期限已过', superseded: '已被新版本替换',
};

function describeTask(task) {
  return `${task.filename} · ${stateLabels[task.state] || task.state} · 已接收 ${(task.offset / 1048576).toFixed(1)} / ${(task.size / 1048576).toFixed(1)} MiB · 服务器登记 ${task.received_at} · 续传至 ${task.expires_at}${task.last_error ? ` · ${task.last_error}` : ''}`;
}

function renderProject(data) {
  const badge = document.querySelector('[data-project-badge]');
  if (badge) { badge.textContent = `${data.label} · v${data.revision}`; badge.className = `tag state-${data.state}`; }
  const target = projectStatus?.querySelector('[data-submission-state]');
  if (target) target.textContent = `${data.label}${data.received_at ? ` · 服务器登记 ${data.received_at} · 凭证 ${data.receipt_id}` : ''}`;
  const cancel = projectStatus?.querySelector('[data-cancel-queue]');
  if (cancel) cancel.hidden = data.state !== 'queued';
  if (data.state === 'queued') {
    projectForm?.querySelectorAll('button[name=action]').forEach(button => { button.disabled = true; });
  }
}

async function refreshProject() {
  if (!projectStatus) return;
  try { renderProject(await request(projectStatus.dataset.projectStatusUrl)); }
  catch (error) {
    console.error('Could not query hackathon submission', error);
    projectStatus.querySelector('[data-submission-state]').textContent = `查询暂时失败：${error.message}，请稍后重试。`;
  }
}
projectStatus?.querySelector('[data-refresh-submission]')?.addEventListener('click', refreshProject);
projectStatus?.querySelector('[data-cancel-queue]')?.addEventListener('click', async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    renderProject(await request(button.dataset.cancelQueue, { method: 'POST' }));
    projectStatus.querySelector('[data-submission-state]').textContent += ' · 排队提交已取消，上传任务仍保留。需要编辑时请刷新页面。';
  } catch (error) {
    console.error('Could not cancel queued submission', error);
    projectStatus.querySelector('[data-submission-state]').textContent = error.message;
  } finally { button.disabled = false; }
});

projectForm?.addEventListener('submit', async event => {
  event.preventDefault();
  const action = event.submitter?.value || 'save';
  const body = new FormData(projectForm);
  body.set('action', action);
  const clickedAt = new Date().toISOString();
  body.set('client_clicked_at', clickedAt);
  remember(`hackathon-submit-click:${location.pathname}`, JSON.stringify({ clickedAt, action, serverAcknowledged: false }));
  const output = projectForm.querySelector('[data-project-status]');
  const buttons = projectForm.querySelectorAll('button[name=action]');
  buttons.forEach(button => { button.disabled = true; });
  let queued = false;
  try {
    // Register the selected file first so this submission cannot omit an in-flight intent.
    if (registration) await registration;
    output.textContent = '正在登记，请等待服务器返回凭证…';
    const data = await request(location.pathname, { method: 'POST', body });
    queued = data.state === 'queued';
    renderProject(data);
    if (projectForm.elements.version) projectForm.elements.version.value = data.revision;
    output.textContent = queued ? '服务器已登记排队提交。请保持页面打开，材料上传完成后自动正式提交。' : '已保存，请在上方核对提交状态。';
    remember(`hackathon-submit-click:${location.pathname}`, JSON.stringify({ clickedAt, ...data }));
    if (!form) location.reload();
  } catch (error) {
    console.error('Could not register hackathon submission', error);
    output.textContent = `${error.message} 尚未确认登记成功，请查询状态或联系技术人员。`;
  } finally {
    if (!queued) buttons.forEach(button => { button.disabled = false; });
  }
});

if (form) {
  const status = form.querySelector('[data-upload-status]');
  const receipt = document.querySelector('[data-upload-receipt]');
  const progress = form.querySelector('[data-upload-progress]');
  const button = form.querySelector('[type=submit]');
  const pause = form.querySelector('[data-pause-upload]');
  const cancel = form.querySelector('[data-cancel-current]');
  let busy = false;
  let controller;
  let currentSession;
  const say = message => { status.textContent = message; };
  const showReceipt = session => {
    receipt.textContent = `服务器已登记：${session.received_at} · 凭证 ${session.id} · 续传至 ${session.expires_at}。${session.expected_checksum ? `SHA-256：${session.expected_checksum}；绑定时间：${session.checksum_bound_at}。` : '文件哈希尚未绑定。'}`;
    const link = document.createElement('a');
    link.href = `${session.status_url}?download=1`; link.textContent = '保存服务器凭证';
    receipt.append(document.createElement('br'), link);
    remember(`hackathon-receipt:${session.id}`, JSON.stringify(session));
  };

  const hashFile = (file, signal) => new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const worker = new Worker(new URL('./upload-hash-worker.js', import.meta.url), { type: 'module' });
    const stop = () => { worker.terminate(); reject(new DOMException('Paused', 'AbortError')); };
    signal.addEventListener('abort', stop, { once: true });
    const finish = () => { worker.terminate(); signal.removeEventListener('abort', stop); };
    worker.addEventListener('message', ({ data }) => {
      if (data.error) { finish(); reject(new UploadError(data.error)); }
      else if (data.checksum) { finish(); resolve(data.checksum); }
      else say(`提交意图已登记，正在校验本地文件 ${Math.round(data.progress)}%…`);
    });
    worker.addEventListener('error', event => {
      console.error('Hackathon hash worker failed', event);
      finish(); reject(new UploadError('文件校验组件未能启动，请刷新页面重试。', 400));
    });
    worker.postMessage(file);
  });

  // Retries only transient failures, with jitter and a fixed receipt expiry.
  const resilient = async (url, options = {}) => {
    let attempt = 0;
    while (true) {
      controller.signal.throwIfAborted();
      if (currentSession && Date.now() >= Date.parse(currentSession.expires_at)) throw new UploadError('续传期限已过，请保存凭证并联系技术人员。', 400);
      try {
        return await request(url, { ...options, signal: AbortSignal.any([controller.signal, AbortSignal.timeout(60000)]) });
      } catch (error) {
        if (controller.signal.aborted || (error instanceof UploadError && error.status !== 429 && !(error.status >= 500))) throw error;
        console.warn('Hackathon upload waiting to retry', error);
        const seconds = Math.max(error.retryAfter || 1, Math.min(30, 2 ** Math.min(++attempt, 5)));
        say(currentSession ? '已保留服务器凭证，正在排队 / 等待网络恢复。请保持页面打开，也可暂停后选择原文件续传。' : '正在重试登记；尚未收到服务器凭证，本机点击时间仅供沟通参考。');
        await delay((seconds + Math.random() * 3) * 1000, controller.signal);
      }
    }
  };

  pause.addEventListener('click', () => controller?.abort());
  cancel.addEventListener('click', async () => {
    if (!currentSession) return;
    cancel.disabled = true;
    controller?.abort();
    try {
      const result = await request(currentSession.cancel_url, { method: 'POST' });
      say(result.state === 'complete' ? '该文件已完成归档。' : '上传任务已取消；关联的排队提交也已中止。');
      cancel.hidden = true; currentSession = null;
      await refreshProject();
    } catch (error) { console.error('Could not cancel upload', error); say(error.message); }
    finally { cancel.disabled = false; }
  });
  window.addEventListener('beforeunload', event => {
    if (busy) { event.preventDefault(); event.returnValue = ''; }
  });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    const file = form.elements.file.files[0];
    const kind = form.elements.kind.value;
    if (!file || !kind) return;
    const limit = (kind === 'poster' ? 20 : 512) * 1048576;
    if (!file.size || file.size > limit) { say(`请选择非空且不超过 ${limit / 1048576} MiB 的文件。`); return; }
    const clickedAt = new Date().toISOString();
    const key = `hackathon-upload:${form.dataset.user}:${form.dataset.project}:${kind}:${file.name}:${file.size}:${file.lastModified}`;
    let id;
    try { id = localStorage.getItem(key); } catch (error) { console.warn('Upload ID storage unavailable', error); }
    const previous = [...document.querySelectorAll('[data-upload-task]')].find(task => task.dataset.taskState === 'uploading' && task.dataset.filename === file.name && Number(task.dataset.size) === file.size && task.dataset.kind === kind);
    id ||= previous?.dataset.id || newId();
    remember(key, id);
    const localEvidence = JSON.stringify({ id, clickedAt, filename: file.name, size: file.size, serverAcknowledged: false });
    remember(`hackathon-click:${id}`, localEvidence);
    receipt.textContent = `本机点击时间 ${clickedAt} · 尚未获得服务器确认。此记录仅供技术沟通，不代表按时提交成功。`;
    const localLink = document.createElement('a');
    localLink.href = `data:application/json;charset=utf-8,${encodeURIComponent(localEvidence)}`;
    localLink.download = `local-click-${id}.json`; localLink.textContent = '保存本机点击记录';
    receipt.append(document.createElement('br'), localLink);
    busy = true; currentSession = null;
    controller = new AbortController();
    button.disabled = true; pause.hidden = false; progress.hidden = false;
    form.elements.file.disabled = true; form.elements.kind.disabled = true;
    const startData = new URLSearchParams({ id, kind, filename: file.name, size: String(file.size), checksum: '', client_clicked_at: clickedAt });
    try {
      say('正在登记提交意图，尚未开始传输大文件…');
      registration = resilient(form.dataset.startUrl, { method: 'POST', body: startData });
      let session = await registration;
      registration = null;
      if (['cancelled', 'expired', 'superseded'].includes(session.state)) {
        startData.set('id', newId());
        registration = resilient(form.dataset.startUrl, { method: 'POST', body: startData });
        session = await registration;
        registration = null;
      }
      currentSession = session;
      remember(key, session.id); showReceipt(session); cancel.hidden = session.state === 'complete';
      const checksum = await hashFile(file, controller.signal);
      startData.set('id', session.id); startData.set('checksum', checksum);
      session = await resilient(form.dataset.startUrl, { method: 'POST', body: startData });
      currentSession = session; showReceipt(session);
      while (session.state !== 'complete' && session.offset < file.size) {
        controller.signal.throwIfAborted();
        session = await resilient(session.admit_url, { method: 'POST' });
        progress.value = session.offset / file.size * 100;
        say(`正在上传 ${Math.round(progress.value)}% · ${(session.offset / 1048576).toFixed(1)} / ${(file.size / 1048576).toFixed(1)} MiB`);
        try {
          session = await resilient(session.chunk_url, {
            method: 'POST', body: file.slice(session.offset, session.offset + session.chunk_size),
            headers: { 'Content-Type': 'application/octet-stream', 'Upload-Offset': String(session.offset) },
          });
        } catch (error) {
          if (!(error instanceof UploadError) || error.status !== 409) throw error;
          console.warn('Checking server offset after upload conflict', error);
          const previousOffset = session.offset;
          session = await resilient(session.status_url);
          if (session.offset <= previousOffset || ['cancelled', 'expired'].includes(session.state)) throw error;
        }
      }
      say('文件已传完，服务器正在校验并归档，请稍候…');
      session = await resilient(session.finish_url, { method: 'POST' });
      if (session.state === 'superseded') throw new UploadError('该材料已被另一次上传替换，请刷新后核对。', 409);
      try { localStorage.removeItem(key); } catch (error) { console.warn('Could not clear completed upload ID', error); }
      showReceipt(session); progress.value = 100; cancel.hidden = true; currentSession = null;
      const version = projectForm?.elements.version;
      if (version && session.revision === Number(version.value) + 1) version.value = session.revision;
      say('材料已完整保存。已登记排队提交的作品会在材料到齐后自动提交；其他作品仍需点击“正式提交 / 排队登记”。');
      const link = document.createElement('a');
      link.href = session.download_url; link.textContent = `下载核对：${session.stored_filename}`;
      status.append(document.createElement('br'), link);
      if (session.preview_url) {
        const details = document.createElement('details');
        details.dataset.archivePreview = session.preview_url;
        const summary = document.createElement('summary'); summary.textContent = '预览 ZIP 一级目录';
        const content = document.createElement('div'); content.dataset.previewContent = '';
        details.append(summary, content); status.append(details); bindPreview(details);
      }
      await refreshProject();
    } catch (error) {
      console.error('Hackathon upload interrupted', error);
      say(controller.signal.aborted ? '已暂停，服务器已登记的任务会保留至凭证中的期限。重新选择原文件可继续。' : `${error.message} 请保存凭证、查询状态后继续，勿将排队登记误认为上传完成。`);
    } finally {
      registration = null; busy = false; button.disabled = false; pause.hidden = true;
      form.elements.file.disabled = false; form.elements.kind.disabled = false;
    }
  });
}

function bindPreview(details) {
  let loaded = false;
  let loading = false;
  details.addEventListener('toggle', async () => {
    if (!details.open || loaded || loading) return;
    loading = true;
    const output = details.querySelector('[data-preview-content]');
    output.textContent = '正在读取目录…';
    try {
      const data = await request(details.dataset.archivePreview);
      output.textContent = `一级目录共 ${data.total} 项${data.truncated ? '，仅显示前 200 项' : ''}。仅核对名称和声明大小，不代表文件内容已验证。`;
      const list = document.createElement('ul');
      for (const item of data.entries) {
        const row = document.createElement('li');
        row.textContent = `${item.name}${item.directory ? '/' : ''} · ${item.files} 个文件 · ${(item.size / 1024).toFixed(1)} KiB`;
        list.append(row);
      }
      output.append(list); loaded = true;
    } catch (error) {
      console.error('Could not preview ZIP directory', error);
      output.textContent = `${error.message}，关闭后重新展开可重试。`;
    } finally { loading = false; }
  });
}
document.querySelectorAll('[data-archive-preview]').forEach(bindPreview);
document.querySelectorAll('[data-query-upload]').forEach(button => {
  button.addEventListener('click', async () => {
    button.disabled = true;
    try { button.parentElement.querySelector('span').textContent = describeTask(await request(button.dataset.queryUpload)); }
    catch (error) { console.error('Could not query upload', error); button.parentElement.querySelector('span').textContent = error.message; }
    finally { button.disabled = false; }
  });
});
document.querySelectorAll('[data-cancel-upload]').forEach(button => {
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      await request(button.dataset.cancelUpload, { method: 'POST' });
      button.parentElement.querySelector('span').textContent = '上传任务已取消，关联的排队提交已中止。';
      button.remove(); await refreshProject();
    } catch (error) {
      console.error('Could not cancel upload', error);
      button.disabled = false; button.parentElement.querySelector('span').textContent = error.message;
    }
  });
});
