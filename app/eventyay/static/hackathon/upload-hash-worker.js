import { SHA256 } from './upload-hash.js';
self.addEventListener('message', async ({ data: file }) => {
  try {
    const hash = new SHA256();
    const size = 4 * 1024 * 1024;
    for (let offset = 0; offset < file.size; offset += size) {
      hash.update(new Uint8Array(await file.slice(offset, offset + size).arrayBuffer()));
      self.postMessage({ progress: Math.min(100, ((offset + size) / file.size) * 100) });
    }
    self.postMessage({ checksum: hash.digest() });
  } catch (error) {
    console.error('Could not hash selected hackathon file', error);
    self.postMessage({ error: '读取文件失败，请重新选择文件。' });
  }
});
