// Browser transmission is separate from durable server acknowledgement.
export class TransferError extends Error {
  constructor(message, status, retryAfter = 5) {
    super(message); this.status = status; this.retryAfter = retryAfter;
  }
}

export function formatRate(rate) {
  if (!Number.isFinite(rate) || rate <= 0) return '0 KiB/s';
  return rate >= 1048576 ? `${(rate / 1048576).toFixed(2)} MiB/s` : `${(rate / 1024).toFixed(1)} KiB/s`;
}

export function formatTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '估算中…';
  const value = Math.ceil(seconds);
  if (value < 60) return `${value} 秒`;
  if (value < 3600) return `${Math.floor(value / 60)} 分 ${value % 60} 秒`;
  return `${Math.floor(value / 3600)} 小时 ${Math.floor(value % 3600 / 60)} 分`;
}

export class TransferMeter {
  constructor(total, confirmed = 0, clock = () => performance.now()) {
    this.total = total; this.confirmed = confirmed; this.clock = clock;
    this.started = clock(); this.sent = confirmed; this.wire = 0;
    this.samples = [{ at: this.started, bytes: 0 }];
    this.partLoaded = 0; this.partOffset = confirmed;
  }
  begin(offset) {
    this.confirmed = offset; this.partOffset = offset; this.partLoaded = 0; this.sent = offset;
  }
  update(loaded) {
    const count = Math.max(this.partLoaded, loaded);
    this.wire += count - this.partLoaded;
    this.partLoaded = count; this.sent = Math.min(this.total, this.partOffset + count);
    this.sample();
  }
  acknowledge(offset) { this.confirmed = offset; this.sent = offset; }
  sample() {
    const now = this.clock();
    this.samples.push({ at: now, bytes: this.wire });
    // Keep the point immediately before the eight-second window.
    while (this.samples.length > 2 && this.samples[1].at < now - 8000) this.samples.shift();
    // Progress events can be frequent; retain a bounded sample history.
    if (this.samples.length > 200) this.samples.splice(1, this.samples.length - 200);
  }
  snapshot() {
    this.sample();
    const now = this.clock(), first = this.samples[0];
    const seconds = (now - first.at) / 1000;
    const rate = seconds >= 0.4 ? (this.wire - first.bytes) / seconds : 0;
    return { rate, sent: this.sent, confirmed: this.confirmed, elapsed: (now - this.started) / 1000,
      eta: rate > 0 && this.sent < this.total ? (this.total - this.sent) / rate : null };
  }
}

export function sendChunk(url, body, { method = 'POST', headers = {}, signal, onProgress, json = true,
  createXHR = () => new XMLHttpRequest() } = {}) {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const xhr = createXHR();
    let idle, settled = false;
    const done = (error, result) => {
      if (settled) return;
      settled = true; clearTimeout(idle); signal.removeEventListener('abort', abort);
      if (error) reject(error); else resolve(result);
    };
    const abort = () => { xhr.abort(); done(signal.reason || new DOMException('Paused', 'AbortError')); };
    const touch = () => {
      clearTimeout(idle);
      idle = setTimeout(() => { done(new TypeError('网络暂时没有响应，请稍后续传。')); xhr.abort(); }, 45000);
    };
    xhr.open(method, url);
    xhr.timeout = 600000; // Slow but active transfers must not fail every 60 seconds.
    for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value);
    xhr.upload.addEventListener('progress', event => { touch(); onProgress?.(event.loaded); });
    xhr.upload.addEventListener('load', touch);
    xhr.addEventListener('progress', touch);
    xhr.addEventListener('load', () => {
      const retry = Math.max(1, Math.min(60, Number(xhr.getResponseHeader('Retry-After')) || 5));
      let data;
      if (json) {
        try { data = JSON.parse(xhr.responseText); }
        catch { done(new TransferError('登录已过期或服务暂时不可用，请重新登录后选择原文件继续。', xhr.status, retry)); return; }
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        done(new TransferError(data?.error || `上传请求失败（${xhr.status}），请稍后续传。`, xhr.status, retry));
      } else { onProgress?.(body.size); done(null, data); }
    });
    xhr.addEventListener('error', () => done(new TypeError('上传连接中断，请检查网络后继续。')));
    xhr.addEventListener('timeout', () => done(new TypeError('分块传输超时，请选择原文件续传。')));
    xhr.addEventListener('abort', () => done(signal.reason || new DOMException('Paused', 'AbortError')));
    signal.addEventListener('abort', abort, { once: true });
    touch();
    try { xhr.send(body); } catch (error) { done(error); }
  });
}
