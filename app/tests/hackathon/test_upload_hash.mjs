// Run from app/: node tests/hackathon/test_upload_hash.mjs
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../../eventyay/static/hackathon/upload-hash.js', import.meta.url));
const { SHA256 } = await import(`data:text/javascript;base64,${source.toString('base64')}`);
for (const size of [0, 1, 3, 55, 56, 63, 64, 65, 127, 128, 129, 1048577, 8388609]) {
  const bytes = randomBytes(size);
  for (const step of [137, 4 * 1024 * 1024]) {
    const hash = new SHA256();
    for (let offset = 0; offset < size; offset += step) hash.update(bytes.subarray(offset, offset + step));
    assert.equal(hash.digest(), createHash('sha256').update(bytes).digest('hex'), `size=${size}, step=${step}`);
  }
}
console.log('SHA-256 verified against Node crypto across block and upload chunk boundaries.');
