/* RUC Makers appearance. The Swiss design system is light only.
 * Load before styles so legacy light rules apply on first paint; the small API
 * stays for scripts that subscribe to appearance changes.
 */
(function () {
  'use strict';
  if (typeof window === 'undefined' || window.rucmakersTheme) return;

  var root = document.documentElement;
  var snapshot = {preference: 'light', resolved: 'light'};

  root.setAttribute('data-theme', 'light');
  root.setAttribute('data-theme-preference', 'light');
  root.classList.toggle('light', true);
  root.classList.toggle('dark', false);
  root.style.colorScheme = 'light';

  try {
    // Drop a stored dark or system choice from the previous design.
    window.localStorage.removeItem('rucmakers-theme');
  } catch (_) { /* Storage may be blocked; the page is light either way. */ }

  window.rucmakersTheme = Object.freeze({
    getPreference: function () { return 'light'; },
    getResolved: function () { return 'light'; },
    setPreference: function () { return snapshot; },
    subscribe: function () { return function () {}; },
  });
})();
