/* Dubai Stem Cell Congress 2026 — Frontend JS */

/* ── Output encoding (shared by every page — do NOT redefine per page) ──
   esc()     escapes the five XML specials, so it is safe in HTML text AND
             inside quoted attributes (the old per-page esc() missed quotes,
             which allowed attribute-injection XSS).
   escAttr() alias, used to make attribute context explicit at call sites.
   safeUrl() for href/src: only http(s)/mailto/tel and relative URLs pass;
             a stored `javascript:` URL can never become a clickable link.
   jsStr()   escapes a string being embedded inside an inline handler. Prefer
             data-* + addEventListener; use this only when unavoidable.      */
const _ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, (c) => _ESC_MAP[c]);
}
const escAttr = esc;
const _SAFE_SCHEME = /^(https?:|mailto:|tel:)/i;
function safeUrl(u) {
  if (u == null) return '';
  const flat = String(u).trim().replace(/[\u0000-\u001F\u007F]/g, '');
  if (!flat) return '';
  if (/^[/#?]/.test(flat) && !flat.startsWith('//')) return esc(flat);
  if (_SAFE_SCHEME.test(flat)) return esc(flat);
  return '';
}
function jsStr(s) {
  return String(s == null ? '' : s)
    .replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"')
    .replace(/\n/g, '\\n').replace(/\r/g, '\\r')
    .replace(/</g, '\\x3C').replace(/>/g, '\\x3E');
}

function showToast(message, type = 'success') {
  document.querySelectorAll('.toast').forEach(t => t.remove());
  const toast = document.createElement('div');
  toast.className = `toast toast-${type} show`;
  // Screen readers announce it; errors are assertive, the rest polite.
  toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
  toast.setAttribute('aria-live', type === 'error' ? 'assertive' : 'polite');
  // Build with textContent, not innerHTML — `message` can carry user-controlled
  // text (notification titles/bodies), so interpolating it into HTML would be
  // a stored-XSS sink.
  const icon = document.createElement('span');
  icon.textContent = type === 'success' ? '✓' : '✕';
  toast.appendChild(icon);
  toast.appendChild(document.createTextNode(' ' + message));
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0'; toast.style.transform = 'translateY(-12px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

/* ── Styled dialogs (replace native confirm/prompt) ──────────────
   confirmDialog(opts|string) → Promise<bool>
   promptDialog(opts|string)  → Promise<string|null>  (null = cancelled)
   opts: {title, message, confirmText, cancelText, danger,
          placeholder, value, textarea} */
function _uiDialog(opts) {
  return new Promise((resolve) => {
    const prev = document.activeElement;
    const overlay = document.createElement('div');
    overlay.className = 'ui-dialog-overlay';
    const box = document.createElement('div');
    box.className = 'ui-dialog';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');

    if (opts.title) {
      const h = document.createElement('h3');
      h.className = 'ui-dialog-title'; h.textContent = opts.title;
      box.setAttribute('aria-label', opts.title); box.appendChild(h);
    }
    if (opts.message) {
      const p = document.createElement('p');
      p.className = 'ui-dialog-msg'; p.textContent = opts.message; box.appendChild(p);
    }
    let field = null;
    if (opts.input) {
      field = document.createElement(opts.textarea ? 'textarea' : 'input');
      field.className = 'ui-dialog-input';
      if (opts.placeholder) field.placeholder = opts.placeholder;
      if (opts.value) field.value = opts.value;
      if (opts.textarea) field.rows = 3;
      box.appendChild(field);
    }
    const actions = document.createElement('div');
    actions.className = 'ui-dialog-actions';
    const cancel = document.createElement('button');
    cancel.type = 'button'; cancel.className = 'ui-dialog-btn';
    cancel.textContent = opts.cancelText || 'Cancel';
    const ok = document.createElement('button');
    ok.type = 'button';
    ok.className = 'ui-dialog-btn primary' + (opts.danger ? ' danger' : '');
    ok.textContent = opts.confirmText || 'Confirm';
    actions.appendChild(cancel); actions.appendChild(ok); box.appendChild(actions);
    overlay.appendChild(box); document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('open'));
    (field || ok).focus();

    function close(val) {
      overlay.classList.remove('open');
      setTimeout(() => overlay.remove(), 180);
      document.removeEventListener('keydown', onKey);
      if (prev && prev.focus) prev.focus();
      resolve(val);
    }
    const doOk = () => close(opts.input ? field.value : true);
    const doCancel = () => close(opts.input ? null : false);
    ok.onclick = doOk; cancel.onclick = doCancel;
    overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) doCancel(); });
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); doCancel(); }
      else if (e.key === 'Enter' && (!opts.textarea || e.ctrlKey || e.metaKey)) {
        e.preventDefault(); doOk();
      } else if (e.key === 'Tab') {
        // Simple focus trap between the two buttons + field.
        const f = [field, cancel, ok].filter(Boolean);
        const i = f.indexOf(document.activeElement);
        e.preventDefault();
        f[(i + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus();
      }
    }
    document.addEventListener('keydown', onKey);
  });
}
function confirmDialog(opts) {
  if (typeof opts === 'string') opts = { message: opts };
  return _uiDialog(opts);
}
function promptDialog(opts) {
  if (typeof opts === 'string') opts = { message: opts };
  return _uiDialog(Object.assign({ input: true }, opts));
}

async function api(url, data) {
  try {
    const res = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data), credentials: 'same-origin',
    });
    const json = await res.json();
    if (!res.ok) {
      const msg = json.detail;
      if (Array.isArray(msg)) throw new Error(msg.map(e => e.msg).join('. '));
      throw new Error(msg || 'Something went wrong');
    }
    return json;
  } catch (err) { throw err; }
}

document.addEventListener('click', (e) => {
  if (e.target.closest('.chip')) e.target.closest('.chip').classList.toggle('selected');
});

function getSelectedChips(container) {
  return Array.from(container.querySelectorAll('.chip.selected')).map(c => c.dataset.value);
}

function observeAnimations() {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) { entry.target.classList.add('animate-up'); observer.unobserve(entry.target); }
    });
  }, { threshold: 0.1 });
  document.querySelectorAll('.observe').forEach(el => observer.observe(el));
}

document.addEventListener('DOMContentLoaded', observeAnimations);
