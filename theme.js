(() => {
  const storageKey = "ai-price-monitor:theme-mode";
  const autoMode = "auto";
  const validModes = new Set([autoMode, "light", "dark"]);

  function getMode() {
    const mode = localStorage.getItem(storageKey) || autoMode;
    return validModes.has(mode) ? mode : autoMode;
  }

  function automaticTheme() {
    const hour = new Date().getHours();
    return hour >= 19 || hour < 7 ? "dark" : "light";
  }

  function effectiveTheme(mode = getMode()) {
    return mode === autoMode ? automaticTheme() : mode;
  }

  function updateControl(control, mode, theme) {
    const manual = control.querySelector(".theme-toggle");
    const automatic = control.querySelector(".theme-auto");
    const themeName = theme === "dark" ? "夜间模式" : "日间模式";
    manual.textContent = theme === "dark" ? "切到日间" : "切到夜间";
    manual.title = `当前为${themeName}，点击后手动切换`;
    automatic.textContent = mode === autoMode ? `跟随时间：${theme === "dark" ? "夜间" : "日间"}` : "跟随时间";
    automatic.classList.toggle("is-auto", mode === autoMode);
    automatic.title = "恢复按电脑时间自动切换（19:00 至次日 07:00 为夜间模式）";
  }

  function applyTheme() {
    const mode = getMode();
    const theme = effectiveTheme(mode);
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll("[data-theme-control]").forEach((control) => updateControl(control, mode, theme));
  }

  function mountControls() {
    document.querySelectorAll("[data-theme-control]").forEach((control) => {
      if (control.dataset.ready) return;
      control.dataset.ready = "true";
      control.classList.add("theme-control");
      control.innerHTML = '<button class="theme-toggle" type="button"></button><button class="theme-auto" type="button">跟随时间</button>';
      control.querySelector(".theme-toggle").addEventListener("click", () => {
        localStorage.setItem(storageKey, effectiveTheme() === "dark" ? "light" : "dark");
        applyTheme();
      });
      control.querySelector(".theme-auto").addEventListener("click", () => {
        localStorage.setItem(storageKey, autoMode);
        applyTheme();
      });
    });
    applyTheme();
  }

  applyTheme();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mountControls);
  else mountControls();
  window.addEventListener("storage", (event) => { if (event.key === storageKey) applyTheme(); });
  window.setInterval(() => { if (getMode() === autoMode) applyTheme(); }, 60000);
})();
