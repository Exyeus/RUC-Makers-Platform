const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');
const source = readFileSync(path.resolve(__dirname, '../../eventyay/static/hackathon/theme-init.js'), 'utf8');

function browser({ stored = null, blocked = false } = {}) {
  const attributes = {}, classes = new Set();
  const storage = new Map(stored === null ? [] : [['rucmakers-theme', stored]]);
  const document = {
    documentElement: { setAttribute: (name, value) => { attributes[name] = value; }, classList: { toggle(name, on) { on ? classes.add(name) : classes.delete(name); } }, style: {} },
  };
  const window = {};
  Object.defineProperty(window, 'localStorage', { get() { if (blocked) throw new Error('storage denied'); return { getItem: key => storage.get(key) ?? null, removeItem: key => storage.delete(key) }; } });
  vm.runInContext(source, vm.createContext({ window, document }));
  return { api: window.rucmakersTheme, attributes, classes, storage, document };
}

test('the page always resolves to the light Swiss appearance before styles load', () => {
  const b = browser();
  assert.equal(b.attributes['data-theme'], 'light');
  assert.equal(b.attributes['data-theme-preference'], 'light');
  assert.equal(b.document.documentElement.style.colorScheme, 'light');
  assert.equal(b.classes.has('light'), true);
  assert.equal(b.classes.has('dark'), false);
});

test('a stored dark choice from the previous design is discarded', () => {
  const b = browser({ stored: 'dark' });
  assert.equal(b.attributes['data-theme'], 'light');
  assert.equal(b.storage.has('rucmakers-theme'), false);
});

test('blocked storage still renders light and keeps the API usable', () => {
  const b = browser({ blocked: true });
  assert.equal(b.attributes['data-theme'], 'light');
  assert.equal(b.api.getResolved(), 'light');
  b.api.setPreference('dark');
  assert.equal(b.api.getPreference(), 'light');
  const unsubscribe = b.api.subscribe(() => {});
  assert.equal(typeof unsubscribe, 'function');
});
