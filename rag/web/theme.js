"use strict";

(() => {
  const key = "esis.theme";
  const device = matchMedia("(prefers-color-scheme: dark)");
  let selected;
  try {
    selected = localStorage.getItem(key);
  } catch {
    selected = null;
  }
  if (selected !== "light" && selected !== "dark") selected = null;

  function apply() {
    const theme = selected ?? (device.matches ? "dark" : "light");
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll("[data-theme-value]").forEach((button) => {
      button.setAttribute("aria-pressed", String(theme === button.dataset.themeValue));
    });
  }

  apply();
  device.addEventListener("change", apply);
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-theme-value]").forEach((button) => {
      button.addEventListener("click", () => {
        selected = button.dataset.themeValue;
        try {
          localStorage.setItem(key, selected);
        } catch {
          // A browser with storage disabled can still switch themes for this session.
        }
        apply();
      });
    });
    apply();
  });
})();
