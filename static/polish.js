'use strict';
/* Interaction polish shared by the landlord workspace and the tenant portal:
   animated figures, a collapsible sidebar with tooltips, and a topbar that lifts on scroll. */
(() => {
  const store = {
    get: k => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } }
  };
  const root = document.documentElement;
  if (store.get('haven.rail') === '1') root.classList.add('rail');

  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const seen = new Set();
  const MONEY = /^(€\s?)(-?[\d,]+)(\.\d+)?$/, PLAIN = /^-?[\d,]+(\.\d+)?%?$/;
  function countUp(el) {
    const text = el.textContent.trim();
    if (el.dataset.cu || reduce || document.documentElement.lang === 'nl') return;
    const label = (el.closest('.stat,.dash-card,.th-hero')?.querySelector('.stat-top,.th-kicker')?.textContent || '') + '|' + text;
    el.dataset.cu = '1';
    if (seen.has(label)) return;
    seen.add(label);
    const m = text.match(MONEY);
    let prefix = '', suffix = '', decimals = 0, target;
    if (m) { prefix = m[1]; decimals = (m[3] || '.').length - 1; target = parseFloat((m[2] + (m[3] || '')).replace(/,/g, '')); }
    else if (PLAIN.test(text)) { suffix = text.endsWith('%') ? '%' : ''; const body = text.replace('%', ''); decimals = (body.split('.')[1] || '').length; target = parseFloat(body.replace(/,/g, '')); }
    else return;
    if (!isFinite(target) || target === 0) return;
    const fmt = v => prefix + v.toLocaleString('en-US', {minimumFractionDigits: decimals, maximumFractionDigits: decimals}) + suffix;
    const t0 = performance.now(), dur = 700;
    let written = text;
    const step = now => {
      // Another script changed this figure (e.g. the month picker): leave its value alone.
      if (!el.isConnected || el.textContent !== written) return;
      const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      written = p < 1 ? fmt(target * e) : text;
      el.textContent = written;
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  const RAIL_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/></svg>';
  function enhance() {
    document.querySelectorAll('.stat-value:not([data-cu]),.th-amount:not([data-cu])').forEach(countUp);
    document.querySelectorAll('.nav-item:not([data-tip])').forEach(b => { b.dataset.tip = b.title = b.querySelector('span:not(.count)')?.textContent.trim() || ''; });
    const bar = document.querySelector('.topbar');
    if (bar && !bar.querySelector('.rail-toggle')) {
      const btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'rail-toggle'; btn.setAttribute('aria-label', 'Collapse or expand the menu'); btn.title = 'Collapse menu'; btn.innerHTML = RAIL_ICON;
      const mobile = bar.querySelector('.mobile-menu');
      if (mobile) mobile.after(btn); else bar.prepend(btn);
    }
  }
  document.addEventListener('click', ev => {
    if (!ev.target.closest('.rail-toggle')) return;
    root.classList.toggle('rail');
    store.set('haven.rail', root.classList.contains('rail') ? '1' : '0');
  });

  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; enhance(); });
  }).observe(document.body, {childList: true, subtree: true});
  addEventListener('scroll', () => document.body.classList.toggle('scrolled', scrollY > 8), {passive: true});
  enhance();
})();
