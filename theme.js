(() => {
  'use strict';
  const key = 'ai-price-monitor:theme-mode';
  const modes = new Set(['light', 'dark', 'auto']);
  let memory = 'auto';
  let transitionTimer;
  let channel;
  try { channel = new BroadcastChannel('linx-theme'); } catch (_) {}

  function mode() {
    try {
      const saved = localStorage.getItem(key);
      if (modes.has(saved)) memory = saved;
    } catch (_) {}
    return memory;
  }
  function scheduledTheme() {
    const hour = new Date().getHours();
    return hour >= 19 || hour < 7 ? 'dark' : 'light';
  }
  function apply(animate = false) {
    const selected = mode();
    const theme = selected === 'auto' ? scheduledTheme() : selected;
    const root = document.documentElement;
    const previous = root.dataset.theme;
    clearTimeout(transitionTimer);
    root.classList.remove('theme-transitioning', 'theme-transitioning-active');
    const motion = typeof matchMedia === 'function' && !matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (animate && previous && previous !== theme && motion) {
      root.classList.add('theme-transitioning');
      requestAnimationFrame(() => root.classList.add('theme-transitioning-active'));
      transitionTimer = setTimeout(() => root.classList.remove('theme-transitioning', 'theme-transitioning-active'), 820);
    }
    root.dataset.theme = theme;
    root.dataset.themeMode = selected;
    document.querySelectorAll('[data-theme-control] button[data-theme-mode]').forEach(button => {
      const active = button.dataset.themeMode === selected;
      button.setAttribute('aria-pressed', String(active));
      button.classList.toggle('is-active', active);
      button.classList.toggle('is-auto', active && selected === 'auto');
      if (button.dataset.themeMode === 'auto') {
        button.title = '按本机时间自动切换：19:00 至次日 07:00 为夜间；当前为' + (theme === 'dark' ? '夜间' : '日间');
      }
    });
  }
  function select(value) {
    if (!modes.has(value)) return;
    memory = value;
    try { localStorage.setItem(key, value); } catch (_) {}
    apply(true);
    try { channel?.postMessage({ mode: value }); } catch (_) {}
  }
  function mount() {
    document.querySelectorAll('[data-theme-control]').forEach(control => {
      if (control.dataset.themeReady === 'true') return;
      control.dataset.themeReady = 'true';
      control.classList.add('theme-control');
      control.setAttribute('role', 'group');
      control.setAttribute('aria-label', '页面显示模式');
      control.innerHTML = '<button type="button" class="theme-light" data-theme-mode="light" title="始终使用日间模式">日间</button><button type="button" class="theme-dark" data-theme-mode="dark" title="始终使用夜间模式">夜间</button><button type="button" class="theme-auto" data-theme-mode="auto">跟随时间</button>';
      control.querySelectorAll('button[data-theme-mode]').forEach(button => {
        button.addEventListener('click', () => select(button.dataset.themeMode));
      });
    });
    apply();
  }

  // Apply the stored theme immediately, even when this script is deferred and
  // placeholders exist but their buttons have not been mounted yet.
  apply();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount, { once: true });
  } else {
    mount();
  }
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) {
      memory = modes.has(event.newValue) ? event.newValue : 'auto';
      apply();
    }
  });
  if (channel) channel.onmessage = event => {
    if (modes.has(event.data?.mode)) {
      memory = event.data.mode;
      apply();
    }
  };
  window.addEventListener('pageshow', mount);
  window.addEventListener('focus', () => apply());
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) apply();
  });
  window.setInterval(() => { if (mode() === 'auto') apply(); }, 30000);
})();
