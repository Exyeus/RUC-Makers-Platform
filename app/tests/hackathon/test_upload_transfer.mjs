import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../../eventyay/static/hackathon/upload-transfer.js', import.meta.url));
const { TransferMeter, formatRate, formatTime, sendChunk } = await import(`data:text/javascript;base64,${source.toString('base64')}`);
let now = 0;
const meter = new TransferMeter(20 * 1048576, 8 * 1048576, () => now);
meter.begin(8 * 1048576);
now = 2000; meter.update(1048576);
assert.equal(meter.snapshot().rate, 524288);
assert.equal(meter.snapshot().eta, 22);
assert.equal(meter.snapshot().confirmed, 8 * 1048576);
assert.equal(meter.snapshot().sent, 9 * 1048576);
meter.begin(8 * 1048576); // Retrying never claims the failed bytes were saved.
assert.equal(meter.snapshot().sent, 8 * 1048576);
now = 4000; meter.update(2 * 1048576); meter.acknowledge(10 * 1048576);
assert.equal(meter.snapshot().confirmed, 10 * 1048576);
now = 14000; assert.equal(meter.snapshot().rate, 0);
assert.equal(meter.snapshot().eta, null);
assert.equal(formatRate(1048576), '1.00 MiB/s');
assert.equal(formatTime(61), '1 分 1 秒');

class FakeXHR extends EventTarget {
  upload = new EventTarget();
  status = 200;
  responseText = '{"offset":4096}';
  headers = {};
  open(method, url) { this.method = method; this.url = url; }
  setRequestHeader(key, value) { this.headers[key] = value; }
  getResponseHeader() { return null; }
  send() {}
  abort() { this.dispatchEvent(new Event('abort')); }
}
const controller = new AbortController(), xhr = new FakeXHR();
const seen = [];
const pending = sendChunk('/chunk', new Blob([new Uint8Array(4096)]), {
  signal: controller.signal, onProgress: n => seen.push(n), createXHR: () => xhr,
});
const event = new Event('progress'); event.loaded = 1024; xhr.upload.dispatchEvent(event);
assert.deepEqual(seen, [1024]); // Visible before the HTTP response or a full 8 MiB chunk.
xhr.dispatchEvent(new Event('load'));
assert.equal((await pending).offset, 4096);
assert.equal(seen.at(-1), 4096);
const aborted = new AbortController();
const stopped = sendChunk('/chunk', new Blob(['x']), { signal: aborted.signal, createXHR: () => new FakeXHR() });
aborted.abort();
await assert.rejects(stopped, { name: 'AbortError' });
console.log('Progress before acknowledgement, resume accounting, speed, ETA, idle decay and pause verified.');
