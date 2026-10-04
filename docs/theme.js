(function () {
  var STORAGE_KEY = 'hub-pages-mode';

  function systemMode() {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark'
      : 'light';
  }

  function getMode() {
    var saved = localStorage.getItem(STORAGE_KEY);
    return saved === 'light' || saved === 'dark' ? saved : systemMode();
  }

  function applyMode(mode) {
    document.documentElement.dataset.mode = mode;

    var banner = document.getElementById('site-banner');
    if (banner && banner.dataset.light && banner.dataset.dark) {
      banner.src = mode === 'dark' ? banner.dataset.dark : banner.dataset.light;
    }

    var btn = document.getElementById('theme-toggle');
    if (btn) {
      var toLight = mode === 'dark';
      btn.textContent = toLight ? 'Light mode' : 'Dark mode';
      btn.setAttribute(
        'aria-label',
        toLight ? 'Switch to light mode' : 'Switch to dark mode'
      );
    }
  }

  function setMode(mode) {
    localStorage.setItem(STORAGE_KEY, mode);
    applyMode(mode);
  }

  window.HubPagesTheme = {
    getMode: getMode,
    setMode: setMode,
    applyMode: applyMode,
    toggle: function () {
      setMode(getMode() === 'dark' ? 'light' : 'dark');
    },
  };

  document.addEventListener('DOMContentLoaded', function () {
    applyMode(getMode());
    var btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.addEventListener('click', function () {
        window.HubPagesTheme.toggle();
      });
    }
  });
})();
