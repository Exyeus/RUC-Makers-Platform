const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');
const source = readFileSync(path.resolve(__dirname, '../../eventyay/static/hackathon/theme-init.js'), 'utf8');

function browser({ stored = null, dark = false, blocked = false, legacy = false, mediaAbsent = false, mediaThrows = false, loading = false } = {}) {
  const attributes = {}, classes = new Set(), windowEvents = {}, documentEvents = {}, events = [], errors = [];
  const selects = [{ value: '' }];
  const storage = new Map(stored === null ? [] : [['rucmakers-theme', stored]]);
  let systemChanged;
  const media = { matches: dark };
  if (legacy) media.addListener = listener => { systemChanged = listener; };
  else media.addEventListener = (_, listener) => { systemChanged = listener; };
  const meta = { content: '#070919', getAttribute: name => ({ 'data-theme-light': '#f1ebf6', 'data-theme-dark': '#070919' })[name] || null };
  const document = {
    readyState: loading ? 'loading' : 'complete',
    documentElement: { setAttribute: (name, value) => { attributes[name] = value; }, classList: { toggle(name, on) { on ? classes.add(name) : classes.delete(name); } }, style: {} },
    querySelectorAll: () => selects,
    querySelector: () => meta,
    addEventListener: (name, listener) => { (documentEvents[name] ||= []).push(listener); },
  };
  const window = {
    addEventListener: (name, listener) => { (windowEvents[name] ||= []).push(listener); },
    dispatchEvent: event => { events.push(event); },
    matchMedia: mediaAbsent ? undefined : () => { if (mediaThrows) throw new Error('OS detection denied'); return media; },
  };
  Object.defineProperty(window, 'localStorage', { get() { if (blocked) throw new Error('storage denied'); return { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) }; } });
  const context = vm.createContext({ window, document, Set, CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } }, console: { error: (...args) => errors.push(args) } });
  vm.runInContext(source, context);
  return {
    api: window.rucmakersTheme, attributes, classes, selects, meta, storage, events, errors, context, document,
    systemChange(on) { media.matches = on; systemChanged?.({ matches: on }); },
    storageChange(key, newValue) { windowEvents.storage.forEach(listener => listener({ key, newValue })); },
    nativeChange(value) { documentEvents.change.forEach(listener => listener({ target: { value, matches: () => true } })); },
    ready() { (documentEvents.DOMContentLoaded || []).forEach(listener => listener()); },
    listenerCount() { return Object.values(windowEvents).reduce((count, values) => count + values.length, 0); },
  };
}

test('system default is applied synchronously with matching color-scheme and controls', () => {
  const b = browser({ dark: true });
  assert.equal(b.api.getPreference(), 'system');
  assert.equal(b.api.getResolved(), 'dark');
  assert.equal(b.attributes['data-theme'], 'dark');
  assert.equal(b.attributes['data-theme-preference'], 'system');
  assert.equal(b.document.documentElement.style.colorScheme, 'dark');
  assert.equal(b.selects[0].value, 'system');
  assert.equal(b.meta.content, '#070919');
});

test('saved explicit preference wins over system and persists future changes', () => {
  const b = browser({ stored: 'light', dark: true });
  assert.equal(b.attributes['data-theme'], 'light');
  b.systemChange(false);
  b.api.setPreference('dark');
  assert.equal(b.storage.get('rucmakers-theme'), 'dark');
  assert.equal(b.attributes['data-theme'], 'dark');
  assert.equal(b.classes.has('light'), false);
  assert.equal(b.classes.has('dark'), true);
  b.systemChange(false);
  assert.equal(b.api.getResolved(), 'dark');
});

test('system changes update only system preference including the legacy media listener', () => {
  for (const legacy of [false, true]) {
    const b = browser({ dark: false, legacy });
    b.systemChange(true);
    assert.equal(b.api.getResolved(), 'dark');
    b.systemChange(false);
    assert.equal(b.api.getResolved(), 'light');
  }
});

test('invalid storage and invalid API choices safely fall back to system', () => {
  const b = browser({ stored: '<script>invalid</script>', dark: true });
  assert.equal(b.api.getPreference(), 'system');
  assert.equal(b.api.getResolved(), 'dark');
  b.api.setPreference('light');
  b.api.setPreference('invalid');
  assert.equal(b.api.getPreference(), 'system');
  assert.equal(b.attributes['data-theme'], 'dark');
});

test('blocked storage keeps a usable in-memory choice and system fallback', () => {
  const b = browser({ blocked: true, dark: false });
  assert.equal(b.attributes['data-theme'], 'light');
  b.api.setPreference('dark');
  assert.equal(b.api.getResolved(), 'dark');
  b.api.setPreference('system');
  b.systemChange(true);
  assert.equal(b.api.getResolved(), 'dark');
});

test('cross-tab storage and clear events synchronize while unrelated keys are ignored', () => {
  const b = browser({ dark: false });
  b.storageChange('rucmakers-theme', 'dark');
  assert.equal(b.api.getResolved(), 'dark');
  b.storageChange('unrelated', 'light');
  assert.equal(b.api.getResolved(), 'dark');
  b.storageChange(null, null);
  assert.equal(b.api.getPreference(), 'system');
  assert.equal(b.api.getResolved(), 'light');
});

test('native select remains functional without React and deferred controls sync on DOM ready', () => {
  const b = browser({ loading: true });
  b.nativeChange('dark');
  assert.equal(b.attributes['data-theme'], 'dark');
  b.selects.push({ value: '' });
  b.ready();
  assert.equal(b.selects[1].value, 'dark');
});

test('subscribers receive changes once, can unsubscribe, and isolate listener failures', () => {
  const b = browser();
  const received = [];
  const stop = b.api.subscribe(value => received.push({ preference: value.preference, resolved: value.resolved }));
  b.api.subscribe(() => { throw new Error('synthetic listener failure'); });
  b.api.setPreference('dark');
  assert.deepEqual(received, [{ preference: 'dark', resolved: 'dark' }]);
  assert.equal(b.errors.length, 1);
  stop();
  b.api.setPreference('light');
  assert.equal(received.length, 1);
  assert.equal(b.events.at(-1).type, 'rucmakers:themechange');
});

test('theme color follows appearance and restores the original dark value', () => {
  const b = browser({ dark: true });
  b.api.setPreference('light');
  assert.equal(b.meta.content, '#f1ebf6');
  b.api.setPreference('dark');
  assert.equal(b.meta.content, '#070919');
});

test('duplicate bootstrap keeps the same API and does not add event listeners', () => {
  const b = browser(), api = b.api, count = b.listenerCount();
  vm.runInContext(source, b.context);
  assert.equal(b.context.window.rucmakersTheme, api);
  assert.equal(b.listenerCount(), count);
});

test('missing matchMedia resolves system safely to light', () => {
  const b = browser({ mediaAbsent: true });
  assert.equal(b.api.getResolved(), 'light');
});

test('blocked OS appearance detection still permits a manual preference', () => {
  const b = browser({ mediaThrows: true });
  assert.equal(b.api.getResolved(), 'light');
  b.api.setPreference('dark');
  assert.equal(b.api.getResolved(), 'dark');
});
