/* RUC Makers appearance. Load before styles; safe to execute again in ui.js. */
(function () {
  'use strict';
  if (typeof window === 'undefined' || window.rucmakersTheme) return;

  var root = document.documentElement;
  var storageKey = 'rucmakers-theme';
  var choices = ['light', 'dark', 'system'];
  var listeners = new Set();
  var media = null;
  try {
    if (typeof window.matchMedia === 'function') media = window.matchMedia('(prefers-color-scheme: dark)');
  } catch (_) { /* Explicit light and dark choices still work without system detection. */ }
  var preference = 'system';
  var resolved;
  var lastPreference;
  var darkThemeColor;

  function normalize(value) {
    return choices.indexOf(value) === -1 ? 'system' : value;
  }

  try {
    preference = normalize(window.localStorage.getItem(storageKey));
  } catch (_) {
    // Private browsing and embedded clients may deny storage. In-memory choice
    // and the system appearance remain available for the current page.
  }

  function snapshot() {
    return {preference: preference, resolved: resolved};
  }

  function updateControls() {
    document.querySelectorAll('[data-theme-select]').forEach(function (select) {
      select.value = preference;
      select.disabled = false;
    });
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      if (darkThemeColor === undefined) {
        darkThemeColor = meta.getAttribute('data-theme-dark') || meta.content || '#0b0d14';
      }
      meta.content = resolved === 'light'
        ? (meta.getAttribute('data-theme-light') || '#f3edf8')
        : darkThemeColor;
    }
  }

  function apply() {
    var next = preference === 'system' ? (media && media.matches ? 'dark' : 'light') : preference;
    var changed = next !== resolved || preference !== lastPreference;
    resolved = next;
    lastPreference = preference;
    root.setAttribute('data-theme', resolved);
    root.setAttribute('data-theme-preference', preference);
    root.classList.toggle('light', resolved === 'light');
    root.classList.toggle('dark', resolved === 'dark');
    root.style.colorScheme = resolved;
    updateControls();
    if (changed) {
      listeners.forEach(function (listener) {
        try { listener(snapshot()); } catch (error) { console.error('Appearance listener failed', error); }
      });
      window.dispatchEvent(new CustomEvent('rucmakers:themechange', {detail: snapshot()}));
    }
  }

  function setPreference(value) {
    preference = normalize(value);
    try { window.localStorage.setItem(storageKey, preference); } catch (_) { /* Keep the in-memory choice. */ }
    apply();
  }

  window.rucmakersTheme = Object.freeze({
    getPreference: function () { return preference; },
    getResolved: function () { return resolved; },
    setPreference: setPreference,
    subscribe: function (listener) {
      listeners.add(listener);
      return function () { listeners.delete(listener); };
    },
  });

  // Apply synchronously before the first stylesheet is evaluated.
  apply();

  if (media) {
    var systemChanged = function () { if (preference === 'system') apply(); };
    if (typeof media.addEventListener === 'function') media.addEventListener('change', systemChanged);
    else if (typeof media.addListener === 'function') media.addListener(systemChanged);
  }
  window.addEventListener('storage', function (event) {
    if (event.key === storageKey || event.key === null) {
      preference = normalize(event.newValue);
      apply();
    }
  });
  document.addEventListener('change', function (event) {
    var target = event.target;
    if (target && typeof target.matches === 'function' && target.matches('[data-theme-select]')) {
      setPreference(target.value);
    }
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', updateControls, {once: true});
  else updateControls();
})();
