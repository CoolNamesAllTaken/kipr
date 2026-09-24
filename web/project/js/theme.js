// Dark / light theme. Follows prefers-color-scheme until the user picks one; the pick is remembered
// per browser (localStorage, best effort) and applied as data-theme on <html>, which viewer.css and
// embedded modules (pcba3d) read.
import { el } from './util.js';

const KEY = 'kipr.theme';
let button = null;

function stored() {
  try { const v = localStorage.getItem(KEY); return v === 'dark' || v === 'light' ? v : null; } catch { return null; }
}

export function currentTheme() {
  const t = document.documentElement.dataset.theme;
  if (t === 'dark' || t === 'light') return t;
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function initTheme() {
  const t = stored();
  if (t) document.documentElement.dataset.theme = t;
}

export function toggleTheme() {
  const next = currentTheme() === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem(KEY, next); } catch { /* private mode: not remembered */ }
  if (button) button.textContent = label();
  window.dispatchEvent(new CustomEvent('kipr-theme', { detail: next }));
}

const label = () => (currentTheme() === 'dark' ? '☀ Light' : '☾ Dark');

export function themeButton() {
  button = el('button', { class: 'btn small', title: 'Toggle dark / light (t)', onclick: () => toggleTheme() }, label());
  return button;
}
