import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmdirSync, unlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';

// Native DOM/media regression; intentionally isolated from real accounts, uploads and OSS.
const require = createRequire(new URL('../../../tools/quality-browser/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const transfer = readFileSync(new URL('../../eventyay/static/hackathon/upload-transfer.js', import.meta.url));
const transferURL = `data:text/javascript;base64,${transfer.toString('base64')}`;
const upload = readFileSync(new URL('../../eventyay/static/hackathon/upload.js', import.meta.url), 'utf8')
  .replace("'./upload-transfer.js?v=20261116.2'", JSON.stringify(transferURL));
const media = readFileSync(new URL('../../eventyay/static/hackathon/media-player.js', import.meta.url), 'utf8');
const clipDirectory = mkdtempSync(join(tmpdir(), 'ruc-media-refresh-'));
const clipPath = join(clipDirectory, 'clip.mp4');
let clip;
try {
  execFileSync('ffmpeg', [
    '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=32x32:r=10', '-t', '3', '-an',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', clipPath,
  ]);
  clip = readFileSync(clipPath);
} finally {
  try { unlinkSync(clipPath); } finally { rmdirSync(clipDirectory); }
}
const item = {
  asset_id: 'asset-one', checksum: 'checksum-a', kind_key: 'preliminary', kind: '初评短视频',
  filename: 'clip.mp4', original_filename: 'clip.mp4', size_display: '2 KiB',
  download_url: '/download/one', media_source_url: '/source/one', media_error_url: '/support',
  media_probe: {state: 'verified', duration_seconds: 3, has_audio: false},
};
const html = `<!doctype html><html><body><div data-asset-list>
<a href="/download/one">original download</a>
<section class="private-media" data-media-player data-asset-id="asset-one" data-checksum="checksum-a" data-source-url="/source/one">
<h3>初评短视频 · clip.mp4</h3><video controls playsinline preload="metadata"></video>
<p data-media-status role="status">点击加载</p><div class="actions"><button type="button" data-media-load>加载 / 恢复播放</button><a href="/support">报告播放异常</a></div>
</section><small>original probe</small></div></body></html>`;
const browser = await chromium.launch({headless: true, channel: 'chromium'});
try {
  const page = await browser.newPage();
  let authorizations = 0;
  let downloads = 0;
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('http://media.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (url.pathname.startsWith('/source/')) {
      authorizations++;
      return route.fulfill({contentType: 'application/json', body: JSON.stringify({url: `/video?token=${authorizations}`})});
    }
    if (url.pathname === '/video') return route.fulfill({contentType: 'video/mp4', body: clip});
    downloads++;
    return route.fulfill({status: 404});
  });
  await page.goto('http://media.test/');
  await page.addScriptTag({type: 'module', content: media});
  await page.addScriptTag({type: 'module', content: `${upload}\nwindow.renderTestMaterials = renderMaterials;`});
  await page.waitForFunction(() => typeof window.renderTestMaterials === 'function');
  assert.equal(authorizations, 0, 'Rendering must not obtain/download a private source automatically.');
  await page.locator('[data-media-load]').click();
  await page.waitForFunction(() => document.querySelector('video').readyState >= 2);
  await page.evaluate(async () => {
    window.originalVideo = document.querySelector('video');
    window.originalPlayer = document.querySelector('[data-media-player]');
    await new Promise(resolve => {
      originalVideo.addEventListener('seeked', resolve, {once: true});
      originalVideo.currentTime = 0.5;
    });
    await originalVideo.play();
  });
  await page.waitForFunction(() => originalVideo.currentTime >= 0.49 && !originalVideo.paused);
  const retained = await page.evaluate(material => {
    const before = originalVideo.currentTime;
    window.renderTestMaterials(document.querySelector('[data-asset-list]'), [material]);
    return {
      sameVideo: document.querySelector('video') === originalVideo,
      samePlayer: document.querySelector('[data-media-player]') === originalPlayer,
      time: originalVideo.currentTime, before,
      playing: !originalVideo.paused,
    };
  }, item);
  console.log('retained media state', JSON.stringify(retained));
  assert.equal(retained.sameVideo, true);
  assert.equal(retained.samePlayer, true);
  assert.equal(retained.playing, true);
  assert.ok(retained.time >= retained.before - 0.01 && retained.time >= 0.49, 'Status refresh must retain the live playback position.');
  assert.equal(authorizations, 1);
  await page.evaluate(async material => {
    originalVideo.pause();
    await new Promise(resolve => {
      originalVideo.addEventListener('seeked', resolve, {once: true});
      originalVideo.currentTime = 0.75;
    });
    window.savedPosition = originalVideo.currentTime;
    originalVideo.dispatchEvent(new Event('timeupdate'));
    for (let count = 0; count < 3; count++) window.renderTestMaterials(document.querySelector('[data-asset-list]'), [material]);
  }, item);
  await page.locator('[data-media-load]').click();
  await page.waitForFunction(() => document.querySelector('video').readyState >= 1 && Math.abs(document.querySelector('video').currentTime - savedPosition) <= 0.11);
  assert.equal(authorizations, 2, 'Repeated reconciliation must not duplicate authorization click handlers.');

  const changed = {...item, checksum: 'checksum-b', original_filename: '<img src=x onerror="throw 1">.mp4'};
  const replaced = await page.evaluate(material => {
    window.renderTestMaterials(document.querySelector('[data-asset-list]'), [material]);
    return {
      newVideo: document.querySelector('video') !== originalVideo,
      oldPaused: originalVideo.paused,
      oldSource: originalVideo.getAttribute('src'),
      freshSource: document.querySelector('video').getAttribute('src'),
      images: document.querySelectorAll('img').length,
      heading: document.querySelector('[data-media-player] h3').textContent,
    };
  }, changed);
  assert.equal(replaced.newVideo, true);
  assert.equal(replaced.oldPaused, true);
  assert.equal(replaced.oldSource, null);
  assert.equal(replaced.freshSource, null);
  assert.equal(replaced.images, 0);
  assert.ok(replaced.heading.includes('<img src=x'));
  assert.equal(authorizations, 2, 'New bytes need a fresh manual load, never the previous signed source.');
  await page.locator('[data-media-load]').click();
  await page.waitForFunction(() => document.querySelector('video').readyState >= 1);
  assert.equal(authorizations, 3, 'MutationObserver must bind the newly added player.');

  const second = {...item, asset_id: 'asset-two', checksum: 'checksum-c', media_source_url: '/source/two'};
  await page.evaluate(materials => window.renderTestMaterials(document.querySelector('[data-asset-list]'), materials), [changed, second]);
  assert.equal(await page.locator('[data-media-player]').count(), 2);
  assert.equal(authorizations, 3);
  await page.locator('[data-media-load]').nth(1).click();
  await page.waitForFunction(() => document.querySelectorAll('video')[1].readyState >= 1);
  assert.equal(authorizations, 4);
  const reordered = await page.evaluate(async materials => {
    const list = document.querySelector('[data-asset-list]');
    const players = [...list.querySelectorAll('[data-media-player]')];
    const videos = players.map(player => player.querySelector('video'));
    await videos[1].play();
    const before = videos[1].currentTime;
    window.renderTestMaterials(list, materials);
    return {
      same: document.querySelectorAll('video')[0] === videos[1] && document.querySelectorAll('video')[1] === videos[0],
      playing: !videos[1].paused,
      before, after: videos[1].currentTime,
    };
  }, [second, changed]);
  assert.equal(reordered.same, true);
  assert.equal(reordered.playing, true);
  assert.ok(reordered.after >= reordered.before - 0.11);
  const fallback = await page.evaluate(async materials => {
    const list = document.querySelector('[data-asset-list]');
    const video = list.querySelector('video');
    const before = video.currentTime;
    Object.defineProperty(list, 'moveBefore', {value: undefined});
    window.renderTestMaterials(list, materials);
    await new Promise(resolve => setTimeout(resolve, 50));
    return {same: list.querySelectorAll('video')[1] === video, playing: !video.paused, before, after: video.currentTime};
  }, [changed, second]);
  assert.equal(fallback.same, true);
  assert.equal(fallback.playing, true);
  assert.ok(fallback.after >= fallback.before - 0.11);
  assert.equal(authorizations, 4, 'Reordering existing media must not reacquire signed sources.');
  await page.evaluate(() => window.renderTestMaterials(document.querySelector('[data-asset-list]'), []));
  assert.equal(await page.locator('video').count(), 0);

  // An authorization response that arrives after replacement must never attach its old URL.
  await page.evaluate(material => window.renderTestMaterials(document.querySelector('[data-asset-list]'), [material]), item);
  await page.evaluate(() => {
    window.savedFetch = window.fetch;
    window.fetch = (url, options) => new Promise(resolve => {
      window.pendingSignal = options.signal;
      window.finishAuthorization = resolve;
    });
    window.pendingPlayer = document.querySelector('[data-media-player]');
    window.pendingVideo = pendingPlayer.querySelector('video');
    pendingPlayer.querySelector('button').click();
  });
  await page.waitForFunction(() => typeof window.finishAuthorization === 'function');
  await page.evaluate(material => window.renderTestMaterials(document.querySelector('[data-asset-list]'), [material]), changed);
  assert.equal(await page.evaluate(() => pendingSignal.aborted), true);
  await page.evaluate(() => {
    finishAuthorization(new Response(JSON.stringify({url: '/video?stale=1'}), {headers: {'content-type': 'application/json'}}));
    window.fetch = savedFetch;
  });
  await page.waitForTimeout(20);
  assert.equal(await page.evaluate(() => pendingVideo.getAttribute('src')), null);
  assert.equal(await page.locator('video').getAttribute('src'), null);
  assert.equal(downloads, 0);
  assert.deepEqual(errors, []);
  console.log('media refresh regression passed: retained playback/seek; manual fresh sources; dynamic idempotent binding; stale request disposal; safe filenames');
} finally {
  await browser.close();
}
