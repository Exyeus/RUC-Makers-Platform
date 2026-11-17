import { TransferError as UploadError, TransferMeter, formatRate, formatTime, sendChunk } from './upload-transfer.js?v=20261116.2';

const form = document.querySelector('[data-upload-form]');
const csrf = document.querySelector('[name=csrfmiddlewaretoken]')?.value;
const projectForm = document.querySelector('[data-project-form]');
const projectStatus = document.querySelector('[data-project-status-url]');
let registration = null;
let savingProject = false;
let latestProjectState = projectStatus?.dataset.projectState;
let activeUploadCount = document.querySelectorAll('[data-upload-task][data-task-state="uploading"]').length;

export function formatEventTime(value) {
  if (!value) return '尚未登记';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间待确认';
  const values = Object.fromEntries(new Intl.DateTimeFormat('zh-CN', {
    timeZone: projectStatus?.dataset.eventTimezone || 'Asia/Shanghai', hourCycle: 'h23',
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(date).map(part => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
}

const projectButtons = () => [...(projectForm?.elements || [])].filter(node => node.tagName === 'BUTTON' && node.name === 'action');
function updateProjectActions() {
  const closed = projectForm?.dataset.submissionsOpen === 'false';
  projectButtons().forEach(button => {
    const allowedLate = button.value === 'submit' && projectForm?.dataset.lateSubmissionAllowed === 'true';
    button.disabled = savingProject || latestProjectState === 'queued' || (closed && !allowedLate);
  });
  const submit = document.querySelector('[data-submit-project]');
  if (submit) submit.textContent = activeUploadCount ? '登记排队提交' : '正式提交当前版本';
  const guidance = document.querySelector('[data-submit-guidance]');
  if (guidance) guidance.textContent = latestProjectState === 'queued'
    ? '本版已登记排队，材料全部校验完成后自动提交。当前尚未完成正式提交；需要修改时先取消排队。'
    : activeUploadCount
      ? '还有材料正在上传。登记后锁定本版信息，材料全部校验完成后自动提交；当前尚未完成正式提交。'
      : '材料保存后仍需正式提交。修改作品或替换附件后，也需要重新提交。';
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
  if (!response.ok) {
    const error = new UploadError(data.error || `请求失败（${response.status}）`, response.status, retryAfter);
    error.fields = data.fields;
    throw error;
  }
  return data;
}

function clearFieldErrors(scope) {
  for (const control of scope.elements) {
    const errorId = control.dataset?.fieldErrorAjax;
    if (!errorId) continue;
    document.getElementById(errorId)?.remove();
    const descriptions = (control.getAttribute('aria-describedby') || '').split(' ').filter(id => id && id !== errorId);
    if (descriptions.length) control.setAttribute('aria-describedby', descriptions.join(' '));
    else control.removeAttribute('aria-describedby');
    control.removeAttribute('aria-invalid');
    delete control.dataset.fieldErrorAjax;
  }
}

// FormData also includes the self-check controls associated from the final section.
function showFieldErrors(scope, fields) {
  let first;
  for (const [name, message] of Object.entries(fields || {})) {
    let control = scope.elements[name];
    if (control && !control.closest) control = control[0];
    const wrap = control?.closest('.form-field');
    if (!wrap) continue;
    const box = document.createElement('div');
    box.id = `${control.id || name}-ajax-error`;
    box.className = 'field-error';
    box.setAttribute('role', 'alert');
    box.dataset.fieldErrorAjax = '';
    box.textContent = message;
    control.dataset.fieldErrorAjax = box.id;
    control.setAttribute('aria-invalid', 'true');
    control.setAttribute('aria-describedby', `${control.getAttribute('aria-describedby') || ''} ${box.id}`.trim());
    wrap.append(box);
    first ||= control;
  }
  return first;
}

export function projectFeedback(action) {
  const save = document.querySelector('[data-project-status]');
  const submit = document.querySelector('[data-submit-status]');
  for (const output of [save, submit]) if (output) output.textContent = '';
  return action === 'submit' ? submit : save;
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
  return `${task.filename} · ${stateLabels[task.state] || task.state} · 已接收 ${(task.offset / 1048576).toFixed(1)} / ${(task.size / 1048576).toFixed(1)} MiB · 服务器登记 ${formatEventTime(task.received_at)} · 续传至 ${formatEventTime(task.expires_at)}${task.last_error ? ` · ${task.last_error}` : ''}`;
}

function svgIcon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#${name}`);
  svg.append(use);
  return svg;
}

function disposeMaterialPlayer(player) {
  // A bound player aborts its own pending authorization request before removing its source.
  const unhandled = player.dispatchEvent(new Event('hackathon:media-dispose', { cancelable: true }));
  if (unhandled) {
    const video = player.querySelector('video');
    video?.pause();
    video?.removeAttribute('src');
    video?.load();
  }
}

function createMaterialPlayer(item) {
  const player = document.createElement('section');
  player.className = 'private-media';
  player.dataset.mediaPlayer = '';
  player.dataset.assetId = String(item.asset_id);
  player.dataset.checksum = item.checksum || '';
  player.dataset.sourceUrl = item.media_source_url;
  const heading = document.createElement('h3');
  heading.textContent = `${item.kind} · ${item.original_filename || item.filename}`;
  const video = document.createElement('video');
  video.controls = true;
  video.playsInline = true;
  video.preload = 'metadata';
  video.referrerPolicy = 'no-referrer';
  video.setAttribute('aria-label', `${item.kind}播放器`);
  const status = document.createElement('p');
  status.dataset.mediaStatus = '';
  status.setAttribute('role', 'status');
  status.textContent = '点击“加载 / 恢复播放”获取私有材料。';
  const actions = document.createElement('div');
  actions.className = 'actions';
  const button = document.createElement('button');
  button.className = 'button secondary small';
  button.type = 'button';
  button.dataset.mediaLoad = '';
  button.textContent = '加载 / 恢复播放';
  const report = document.createElement('a');
  report.className = 'button secondary small';
  report.href = item.media_error_url;
  report.textContent = '报告播放异常';
  actions.append(button, report);
  player.append(heading, video, status, actions);
  return player;
}

// Reconcile around retained player nodes: a status query must not detach the same video or reset its position.
// Uploaded filenames are user-supplied, so every row is built with textContent instead of parsed HTML.
export function renderMaterials(list, materials) {
  if (!list || !Array.isArray(materials)) return;
  const existingPlayers = [...list.querySelectorAll('[data-media-player]')];
  const retained = new Set();
  const nodes = [];
  if (!materials.length) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = '还没有上传材料。';
    nodes.push(empty);
  }
  for (const item of materials) {
    const link = document.createElement('a');
    link.href = item.download_url;
    const copy = document.createElement('span');
    const kind = document.createElement('strong');
    kind.textContent = `${item.kind} · ${item.original_filename || item.filename}`;
    const detail = document.createElement('small');
    detail.textContent = `${item.size_display} · 已保存`;
    copy.append(kind, detail);
    const arrow = document.createElement('span');
    arrow.textContent = '↓';
    arrow.setAttribute('aria-hidden', 'true');
    link.append(svgIcon('i-folder'), copy, arrow);
    nodes.push(link);
    if (['preliminary', 'video'].includes(item.kind_key) && item.asset_id && item.media_source_url) {
      const player = existingPlayers.find(node => !retained.has(node)
        && node.dataset.assetId === String(item.asset_id) && node.dataset.checksum === (item.checksum || ''))
        || createMaterialPlayer(item);
      player.dataset.sourceUrl = item.media_source_url;
      player.querySelector('h3').textContent = `${item.kind} · ${item.original_filename || item.filename}`;
      player.querySelector('video').setAttribute('aria-label', `${item.kind}播放器`);
      const report = player.querySelector('.actions a');
      if (report) report.href = item.media_error_url;
      retained.add(player);
      nodes.push(player);
    }
    if (item.media_probe?.state === 'verified') {
      const probe = document.createElement('small');
      const seconds = Number(item.media_probe.duration_seconds);
      probe.textContent = `视频已校验 · ${Number.isFinite(seconds) ? seconds.toFixed(2) : '待核对'} 秒${item.media_probe.has_audio ? '' : ' · 无声视频'}；请实际播放核对画面与字幕。`;
      nodes.push(probe);
    }
    if (item.preview_url && (item.original_filename || item.filename).toLowerCase().endsWith('.zip')) {
      const preview = document.createElement('details');
      preview.dataset.archivePreview = item.preview_url;
      const summary = document.createElement('summary'); summary.textContent = '预览 ZIP 一级目录';
      const content = document.createElement('div'); content.dataset.previewContent = ''; content.setAttribute('role', 'status');
      preview.append(summary, content); nodes.push(preview); bindPreview(preview);
    }
  }
  for (const player of existingPlayers) if (!retained.has(player)) disposeMaterialPlayer(player);
  // Leave retained media connected while replacing its surrounding download/probe/preview rows.
  for (const node of [...list.childNodes]) if (!retained.has(node)) node.remove();
  let cursor = list.firstChild;
  for (const node of nodes) {
    if (node === cursor) cursor = cursor.nextSibling;
    else if (retained.has(node) && node.parentNode === list) {
      if (typeof list.moveBefore === 'function') list.moveBefore(node, cursor);
      else {
        // Older browsers lack a state-preserving DOM move; resume only media the user was already playing.
        const video = node.querySelector('video');
        const position = video.currentTime;
        const wasPlaying = !video.paused;
        list.insertBefore(node, cursor);
        if (position > 0) video.currentTime = position;
        if (wasPlaying) video.play().catch(() => {
          node.querySelector('[data-media-status]').textContent = '播放位置已保留。请点击播放继续，或使用“加载 / 恢复播放”。';
        });
      }
    } else list.insertBefore(node, cursor);
  }
}

function renderGaps(box, gaps) {
  if (!box || !Array.isArray(gaps)) return;
  const list = box.querySelector('ul');
  list.textContent = '';
  for (const error of gaps) {
    const row = document.createElement('li');
    row.textContent = error;
    list.append(row);
  }
  box.hidden = !gaps.length;
}

function materialSaveStatus(data) {
  return data?.state === 'queued' ? '材料已保存，正在等待其余排队材料完成。'
    : ['submitted', 'approved'].includes(data?.state) ? `材料已保存，第 ${data.revision} 版已正式提交。`
      : data ? '材料已保存，作品尚未正式提交。请完成下方自查并正式提交当前版本。'
        : '材料已保存，作品提交结果尚待确认。请在页首查询最新状态。';
}

export function renderProject(data) {
  const savedMaterial = document.querySelector('[data-material-save-advisory]');
  if (savedMaterial) savedMaterial.textContent = materialSaveStatus(data);
  if (!projectStatus) return;
  latestProjectState = data.state;
  projectStatus.dataset.projectState = data.state;
  projectStatus.dataset.statusKey = data.status_key;
  projectStatus.querySelector('[data-submission-state]').textContent = data.label;
  projectStatus.querySelector('[data-project-revision]').textContent = data.revision;
  projectStatus.querySelector('[data-status-detail]').textContent = data.status_detail;
  const hasReceipt = Boolean(data.receipt_id && data.received_at);
  projectStatus.querySelector('[data-current-received]').hidden = !hasReceipt;
  const currentReceipt = ['submitted', 'approved', 'queued'].includes(data.state);
  projectStatus.querySelector('[data-received-label]').textContent = currentReceipt ? '本版登记时间' : '最近登记时间';
  const time = projectStatus.querySelector('[data-received-time]');
  time.textContent = formatEventTime(data.received_at);
  time.dateTime = data.received_at || '';
  projectStatus.querySelector('[data-receipt-detail]').hidden = !hasReceipt;
  projectStatus.querySelector('[data-receipt-id]').textContent = data.receipt_id || '';
  const download = projectStatus.querySelector('[data-download-submission]');
  download.hidden = !hasReceipt;
  download.textContent = data.state === 'queued' ? '下载排队登记凭证' : currentReceipt ? '下载本版提交凭证' : '下载最近登记凭证';
  renderMaterials(document.querySelector('[data-asset-list]'), data.materials);
  renderGaps(document.querySelector('[data-submission-errors]'), data.gaps);
  const cancel = projectStatus.querySelector('[data-cancel-queue]');
  if (cancel) cancel.hidden = data.state !== 'queued';
  if (typeof data.active_upload_count === 'number') activeUploadCount = data.active_upload_count;
  if (typeof data.submissions_open === 'boolean' && projectForm) projectForm.dataset.submissionsOpen = String(data.submissions_open);
  if (typeof data.late_submission_allowed === 'boolean' && projectForm) projectForm.dataset.lateSubmissionAllowed = String(data.late_submission_allowed);
  if (Array.isArray(data.information_errors)) {
    const gate = document.querySelector('[data-information-gate]');
    const controls = document.querySelector('[data-information-upload]');
    if (gate) {
      gate.hidden = !data.information_errors.length;
      const list = gate.querySelector('ul');
      if (list) list.replaceChildren(...data.information_errors.map(text => { const item = document.createElement('li'); item.textContent = text; return item; }));
    }
    if (controls) controls.disabled = !!data.information_errors.length;
  }
  updateProjectActions();
}

async function refreshProject() {
  if (!projectStatus) return null;
  const feedback = projectStatus.querySelector('[data-status-query-feedback]');
  try {
    const data = await request(projectStatus.dataset.projectStatusUrl);
    renderProject(data);
    feedback.textContent = '已核对最新状态。';
    return data;
  } catch (error) {
    console.error('Could not query hackathon submission', error);
    feedback.textContent = `查询暂时失败：${error.message}。上方保留的是最近一次已确认状态，请稍后重试。`;
    return null;
  }
}
projectStatus?.querySelector('[data-refresh-submission]')?.addEventListener('click', refreshProject);
projectStatus?.querySelector('[data-cancel-queue]')?.addEventListener('click', async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    renderProject(await request(button.dataset.cancelQueue, { method: 'POST' }));
    projectStatus.querySelector('[data-status-query-feedback]').textContent = '排队提交已取消，上传任务仍保留。需要编辑时请刷新页面，读取最新版本后继续。';
  } catch (error) {
    console.error('Could not cancel queued submission', error);
    projectStatus.querySelector('[data-status-query-feedback]').textContent = error.message;
  } finally { button.disabled = false; }
});

projectForm?.addEventListener('submit', async event => {
  event.preventDefault();
  if (savingProject) return;
  const action = event.submitter?.value || 'save';
  const body = new FormData(projectForm);
  body.set('action', action);
  const clickedAt = new Date().toISOString();
  body.set('client_clicked_at', clickedAt);
  remember(`hackathon-submit-click:${location.pathname}`, JSON.stringify({ clickedAt, action, serverAcknowledged: false }));
  const output = projectFeedback(action);
  output.textContent = action === 'submit' ? '正在提交，请等待服务器确认…' : '正在保存草稿…';
  output.scrollIntoView({ block: 'nearest' });
  savingProject = true; updateProjectActions();
  clearFieldErrors(projectForm);
  projectForm.dispatchEvent(new CustomEvent('hackathon:saving'));
  try {
    // Register the selected file first so this submission cannot omit an in-flight intent.
    if (registration) await registration;
    output.textContent = '正在登记，请等待服务器返回凭证…';
    const data = await request(location.pathname, { method: 'POST', body });
    renderProject(data);
    if (projectForm.elements.version) projectForm.elements.version.value = data.revision;
    projectForm.dispatchEvent(new CustomEvent('hackathon:saved', { detail: { saved: true } }));
    output.textContent = data.state === 'queued'
      ? '服务器已登记排队提交。请保持页面打开，材料上传完成后自动正式提交。'
      : action === 'submit' ? `第 ${data.revision} 版已正式提交。可在页首下载本版凭证。` : '草稿已保存，尚未正式提交本次修改。';
    output.scrollIntoView({ block: 'nearest' });
    remember(`hackathon-submit-click:${location.pathname}`, JSON.stringify({ clickedAt, ...data }));
    if (!form) location.reload();
  } catch (error) {
    console.error('Could not register hackathon submission', error);
    projectForm.dispatchEvent(new CustomEvent('hackathon:saved', { detail: { saved: false } }));
    const invalid = showFieldErrors(projectForm, error.fields);
    // A field-level rejection means nothing was written; a missing answer means the outcome is unknown.
    output.textContent = error.fields
      ? `${error.message} 页面内容已保留，修正后可直接重新提交。`
      : `${error.message} 尚未确认登记成功，请查询状态或联系技术人员。`;
    if (invalid) {
      invalid.focus({ preventScroll: true });
      invalid.scrollIntoView({ block: 'center' });
    } else output.scrollIntoView({ block: 'nearest' });
  } finally {
    savingProject = false; updateProjectActions();
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
  let cancelling = false;
  let controller;
  let currentSession;
  let rejectedSession = false;
  let currentUploadKey;
  let meter, meterTimer, phase = 'idle';
  const metrics = form.querySelector('[data-upload-metrics]');
  const speed = form.querySelector('[data-upload-speed]');
  const eta = form.querySelector('[data-upload-eta]');
  const elapsed = form.querySelector('[data-upload-elapsed]');
  const bytes = form.querySelector('[data-upload-bytes]');
  const draw = () => {
    if (!meter) return;
    const data = meter.snapshot();
    progress.value = data.sent / meter.total * 100;
    bytes.textContent = `已发送 ${(data.sent / 1048576).toFixed(1)} / ${(meter.total / 1048576).toFixed(1)} MiB（${Math.floor(progress.value)}%） · 已确认 ${(data.confirmed / 1048576).toFixed(1)} MiB`;
    speed.textContent = phase === 'sending' ? formatRate(data.rate) : '—';
    eta.textContent = phase === 'sending' ? (data.eta === null ? '估算中…' : formatTime(data.eta))
      : phase === 'verifying' ? '正在校验保存' : phase === 'confirming' ? '等待确认' : phase === 'complete' ? '已完成' : phase === 'error' ? '未完成' : phase === 'paused' ? '已暂停' : '等待传输';
    elapsed.textContent = formatTime(data.elapsed);
  };
  const say = message => { status.textContent = message; };
  const showReceipt = session => {
    receipt.textContent = `上传任务登记于 ${formatEventTime(session.received_at)} · 凭证 ${session.id} · 续传至 ${formatEventTime(session.expires_at)}。${session.expected_checksum ? `SHA-256：${session.expected_checksum}；绑定时间：${formatEventTime(session.checksum_bound_at)}。` : '文件哈希尚未绑定。'}`;
    const link = document.createElement('a');
    link.href = `${session.status_url}?download=1`; link.textContent = '下载上传登记凭证';
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
      else say(`上传任务已登记，正在校验所选文件 ${Math.round(data.progress)}%…`);
    });
    worker.addEventListener('error', event => {
      console.error('Hackathon hash worker failed', event);
      finish(); reject(new UploadError('文件校验组件未能启动，请刷新页面重试。', 400));
    });
    worker.postMessage(file);
  });

  // Retries only transient failures, with jitter and a fixed receipt expiry.
  const resilient = async (url, options = {}, transmit = null) => {
    let attempt = 0;
    const { timeout = 60000, ...parameters } = options;
    while (true) {
      controller.signal.throwIfAborted();
      if (currentSession && Date.now() >= Date.parse(currentSession.expires_at)) throw new UploadError('续传期限已过，请保存凭证并联系技术人员。', 400);
      try {
        return transmit ? await transmit(controller.signal) : await request(url, { ...parameters, signal: AbortSignal.any([controller.signal, AbortSignal.timeout(timeout)]) });
      } catch (error) {
        if (controller.signal.aborted || (error instanceof UploadError && error.status !== 429 && !(error.status >= 500))) throw error;
        console.warn('Hackathon upload waiting to retry', error);
        const seconds = Math.max(error.retryAfter || 1, Math.min(30, 2 ** Math.min(++attempt, 5)));
        phase = 'waiting'; draw();
        say(currentSession ? `已保留服务器凭证，${seconds} 秒后重试连接。请保持页面打开，也可暂停后选择原文件续传。` : '正在重试登记；尚未收到服务器凭证，本机点击时间仅供沟通参考。');
        await delay((seconds + Math.random() * 3) * 1000, controller.signal);
      }
    }
  };

  pause.addEventListener('click', () => controller?.abort());
  cancel.addEventListener('click', async () => {
    if (!currentSession || cancelling) return;
    const sessionToCancel = currentSession;
    const keyToClear = currentUploadKey;
    cancelling = true;
    cancel.disabled = true; button.disabled = true;
    form.elements.file.disabled = true; form.elements.kind.disabled = true;
    controller?.abort();
    say('正在取消上传任务，请等待服务器确认后再选择文件。');
    try {
      const result = await request(sessionToCancel.cancel_url, { method: 'POST' });
      const row = [...document.querySelectorAll('[data-upload-task]')].find(item => item.dataset.id === sessionToCancel.id);
      if (row) {
        row.dataset.taskState = result.state;
        row.querySelector('span').textContent = describeTask(result);
      }
      cancel.hidden = true; currentSession = null; rejectedSession = false;
      if (keyToClear) {
        try { localStorage.removeItem(keyToClear); } catch (error) { console.warn('Could not clear cancelled upload ID', error); }
      }
      currentUploadKey = null;
      await refreshProject();
      say(result.state === 'complete' ? '该文件已完成归档。' : '上传任务已取消；关联的排队提交也已中止。现在可以选择其他文件上传。');
    } catch (error) {
      console.error('Could not cancel upload', error);
      say(`尚未确认取消成功：${error.message} 请查询状态或重试取消，原任务仍保留。`);
    } finally {
      cancelling = false;
      cancel.disabled = false; button.disabled = busy;
      form.elements.file.disabled = busy; form.elements.kind.disabled = busy;
    }
  });
  window.addEventListener('beforeunload', event => {
    if (busy || cancelling) { event.preventDefault(); event.returnValue = ''; }
  });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || cancelling) return;
    if (rejectedSession) {
      say('上一个文件未通过校验。请先点击“取消此任务并更换文件”，再上传重新导出的文件。');
      return;
    }
    const file = form.elements.file.files[0];
    const kind = form.elements.kind.value;
    if (!kind) { say('请先手动选择材料用途，再上传文件。'); form.elements.kind.focus(); return; }
    if (!file) { say('请选择要上传的文件。'); form.elements.file.focus(); return; }
    const limit = (kind === 'poster' ? 20 : 512) * 1048576;
    if (!file.size || file.size > limit) { say(`请选择非空且不超过 ${limit / 1048576} MiB 的文件。`); return; }
    const clickedAt = new Date().toISOString();
    const key = `hackathon-upload:${form.dataset.user}:${form.dataset.project}:${kind}:${file.name}:${file.size}:${file.lastModified}`;
    currentUploadKey = key;
    let id;
    try { id = localStorage.getItem(key); } catch (error) { console.warn('Upload ID storage unavailable', error); }
    const previous = [...document.querySelectorAll('[data-upload-task]')].find(task => task.dataset.taskState === 'uploading' && task.dataset.filename === file.name && Number(task.dataset.size) === file.size && task.dataset.kind === kind);
    id ||= previous?.dataset.id || newId();
    remember(key, id);
    const localEvidence = JSON.stringify({ id, clickedAt, filename: file.name, size: file.size, serverAcknowledged: false });
    remember(`hackathon-click:${id}`, localEvidence);
    receipt.textContent = `本机点击时间 ${formatEventTime(clickedAt)} · 尚未获得服务器确认。此记录仅供技术沟通，不代表按时提交成功。`;
    const localLink = document.createElement('a');
    localLink.href = `data:application/json;charset=utf-8,${encodeURIComponent(localEvidence)}`;
    localLink.download = `local-click-${id}.json`; localLink.textContent = '保存本机点击记录';
    receipt.append(document.createElement('br'), localLink);
    busy = true; currentSession = null; rejectedSession = false; cancel.textContent = '取消本次上传';
    controller = new AbortController();
    button.disabled = true; pause.hidden = false; progress.hidden = false; progress.value = 0;
    metrics.hidden = true; meter = null; phase = 'preparing';
    form.elements.file.disabled = true; form.elements.kind.disabled = true;
    const startData = new URLSearchParams({ id, kind, filename: file.name, size: String(file.size), checksum: '', client_clicked_at: clickedAt, transport: 'direct' });
    try {
      say('正在登记上传任务，尚未开始传输文件…');
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
      await refreshProject();
      const checksum = await hashFile(file, controller.signal);
      say('本地校验完成，正在连接上传通道…');
      startData.set('id', session.id); startData.set('checksum', checksum);
      session = await resilient(form.dataset.startUrl, { method: 'POST', body: startData });
      currentSession = session; showReceipt(session);
      meter = new TransferMeter(file.size, session.offset);
      metrics.hidden = false; draw(); meterTimer = setInterval(draw, 500);
      let directParts = [];
      let signatureRefreshes = 0;
      while (session.state !== 'complete' && session.offset < file.size) {
        controller.signal.throwIfAborted();
        const offset = session.offset;
        let directPart;
        if (session.transport === 'direct') {
          directPart = directParts.find(part => part.offset === offset);
          if (!directPart) {
            phase = 'connecting'; say('正在连接上传通道…'); draw();
            const prepared = await resilient(session.direct_url, { method: 'POST' });
            session = prepared.session; currentSession = session; directParts = prepared.parts;
            if (session.offset !== offset) continue;
            directPart = directParts.find(part => part.offset === offset);
            if (!directPart) throw new UploadError('上传通道未能准备好，请稍后续传。', 503);
          }
        }
        const body = file.slice(offset, offset + session.chunk_size);
        const url = directPart?.url || session.chunk_url;
        const headers = directPart ? {} : {
          'Content-Type': 'application/octet-stream', 'Upload-Offset': String(offset), 'X-CSRFToken': csrf, Accept: 'application/json',
        };
        try {
          const transferred = await resilient(url, {}, signal => {
            meter.begin(offset); phase = 'sending';
            say(session.transport === 'direct' ? '正在上传文件…' : '正在上传文件（兼容通道）…'); draw();
            return sendChunk(url, body, {
              method: directPart ? 'PUT' : 'POST', headers, signal, json: !directPart,
              onProgress: loaded => { meter.update(loaded); draw(); },
            });
          });
          phase = 'confirming'; say('本分块已发送，正在确认保存…'); draw();
          session = directPart
            ? await resilient(session.ack_url, { method: 'POST', body: new URLSearchParams({ offset: String(offset) }) })
            : transferred;
          currentSession = session; meter.acknowledge(session.offset); draw(); signatureRefreshes = 0;
        } catch (error) {
          if (directPart && error instanceof UploadError && error.status === 403 && signatureRefreshes++ < 2) {
            directParts = []; phase = 'connecting'; say('正在更新上传通道，请稍候…'); continue;
          }
          if (!(error instanceof UploadError) || error.status !== 409) throw error;
          console.warn('Checking server offset after upload conflict', error);
          session = await resilient(session.status_url); currentSession = session;
          if (session.offset <= offset || ['cancelled', 'expired'].includes(session.state)) throw error;
          meter.acknowledge(session.offset); directParts = []; draw();
        }
      }
      phase = 'verifying'; draw();
      say('文件已传完，服务器正在校验并归档，请稍候…');
      session = await resilient(session.finish_url, { method: 'POST', timeout: 600000 });
      if (session.state === 'superseded') throw new UploadError('该材料已被另一次上传替换，请刷新后核对。', 409);
      try { localStorage.removeItem(key); } catch (error) { console.warn('Could not clear completed upload ID', error); }
      showReceipt(session); progress.value = 100; cancel.hidden = true; currentSession = null;
      phase = 'complete'; meter.acknowledge(file.size); draw();
      const version = projectForm?.elements.version;
      if (version && session.revision === Number(version.value) + 1) version.value = session.revision;
      const savedProject = await refreshProject();
      say('');
      const savedMaterial = document.createElement('span');
      savedMaterial.dataset.materialSaveAdvisory = '';
      savedMaterial.textContent = materialSaveStatus(savedProject);
      status.append(savedMaterial);
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
    } catch (error) {
      console.error('Hackathon upload interrupted', error);
      const invalidFile = currentSession?.state === 'uploading' && error instanceof UploadError && error.status === 400;
      phase = controller.signal.aborted ? 'paused' : 'error'; draw();
      if (cancelling) {
        // The cancellation owner will report its confirmed outcome.
      } else if (invalidFile && currentSession) {
        rejectedSession = true; cancel.hidden = false; cancel.textContent = '取消此任务并更换文件';
        say(`上传未完成：${error.message} 已停止重试，当前文件尚未保存。请取消此任务，再重新导出或选择其他文件；已经保存的其他材料不受影响。`);
      } else {
        say(controller.signal.aborted ? '已暂停，服务器已登记的任务会保留至凭证中的期限。重新选择原文件可继续。' : `${error.message} 结果尚待确认，请查询状态后继续；上传登记不等于正式提交。`);
      }
    } finally {
      clearInterval(meterTimer);
      registration = null; busy = false; button.disabled = cancelling; pause.hidden = true;
      form.elements.file.disabled = cancelling; form.elements.kind.disabled = cancelling;
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
