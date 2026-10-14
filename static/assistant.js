'use strict';
/* Help chat in the bottom-right corner of every signed-in screen (staff, tenant, owner, contractor).
   It asks /api/assistant/chat, which uses Gemini when a key is configured and a built-in guide otherwise.
   The widget lives outside #app, so page renders never remove it. */
(() => {
  const path = location.pathname;
  const portal = path === '/tenant' ? 'tenant' : path === '/owner' ? 'owner' : path === '/contractor' ? 'contractor' : '';
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const ICON_CHAT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/><path d="M9 11h6M9 14h4"/></svg>';
  const ICON_X = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  const ICON_SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>';

  let ui = null, info = null, csrf = '', busy = false, log = [];  // the conversation lives only until the page is refreshed

  const headers = extra => Object.assign({'Content-Type': 'application/json'}, portal ? {'X-Haven-Portal': portal} : {}, extra || {});
  async function ensureCsrf() {
    if (csrf) return csrf;
    const r = await fetch('/api/me', {headers: headers(), credentials: 'same-origin'});
    if (!r.ok) throw new Error('signed-out');
    csrf = (await r.json()).csrf || '';
    return csrf;
  }
  const currentPage = () => (typeof state !== 'undefined' && state && state.page) || document.querySelector('.sidebar .nav-item.active')?.dataset.page || '';

  function navigate(page) {
    ui.panel.classList.remove('open');
    ui.launcher.classList.remove('open');
    const direct = document.querySelector(`.sidebar .nav-item[data-page="${page}"]`);
    if (direct) { direct.click(); return; }
    if (!portal && typeof state !== 'undefined' && typeof render === 'function') {
      const m = document.getElementById('modal'); if (m?.open) m.close();
      state.page = page; state.filter = 'All'; render(); scrollTo(0, 0); return;
    }
    const tab = () => document.querySelector(`.vs-btn[data-page="${page}"]`);
    if (tab()) { tab().click(); return; }
    document.querySelector('.sidebar .nav-item[data-page="messages"]')?.click();
    setTimeout(() => tab()?.click(), 120);
  }

  // **bold** becomes <strong> built with DOM nodes (no innerHTML); any other markdown symbols are tidied away
  function richText(node, text) {
    const clean = String(text || '').replace(/^#{1,6}\s+/gm, '').replace(/^\s*[*-]\s+/gm, '• ').replace(/`/g, '');
    clean.split(/(\*\*[^*]+\*\*)/g).forEach(part => {
      if (/^\*\*[^*]+\*\*$/.test(part)) node.append(el('strong', '', part.slice(2, -2)));
      else if (part) node.append(document.createTextNode(part.replace(/\*/g, '')));
    });
  }

  function addBubble(role, text, actions) {
    const row = el('div', 'hh-row ' + role);
    const bubble = el('div', 'hh-bubble');
    richText(bubble, text);
    row.append(bubble);
    if (actions?.length) {
      const bar = el('div', 'hh-actions');
      actions.forEach(a => { const b = el('button', 'hh-go', a.label + ' →'); b.type = 'button'; b.addEventListener('click', () => navigate(a.page)); bar.append(b); });
      bubble.append(bar);
    }
    ui.list.append(row);
    ui.list.scrollTop = ui.list.scrollHeight;
    return row;
  }
  function chips() {
    ui.chips.textContent = '';
    (info?.suggestions || []).forEach(s => { const b = el('button', 'hh-chip', s); b.type = 'button'; b.addEventListener('click', () => send(s)); ui.chips.append(b); });
  }

  async function send(text) {
    text = (text || ui.input.value).trim();
    if (!text || busy) return;
    busy = true; ui.input.value = ''; ui.send.disabled = true; ui.chips.hidden = true;
    addBubble('user', text);
    const history = log.slice(-8);
    log.push({role: 'user', text});
    const typing = addBubble('bot', '');
    typing.firstChild.classList.add('typing'); typing.firstChild.innerHTML = '<i></i><i></i><i></i>';
    try {
      const token = await ensureCsrf();
      const r = await fetch('/api/assistant/chat', {method: 'POST', credentials: 'same-origin', headers: headers({'X-CSRF-Token': token}), body: JSON.stringify({message: text, history, page: currentPage(), lang: window.havenLang || 'en'})});
      if (!r.ok) throw new Error(r.status === 401 ? 'Please sign in again.' : 'The helper is unavailable right now.');
      const data = await r.json();
      typing.remove();
      const row = addBubble('bot', data.reply, data.actions);
      if (data.ai_unavailable) {
        const note = el('small', 'hh-note-inline', 'The AI is unavailable right now, so this answer comes from the built-in guide.');
        row.firstChild.append(note);
        ui.badge.textContent = 'Quick guide';
      }
      log.push({role: 'bot', text: data.reply});
    } catch (err) {
      typing.remove();
      addBubble('bot', err.message === 'signed-out' ? 'Please sign in first.' : (err.message || 'Something went wrong. Please try again.'));
    }
    busy = false; ui.send.disabled = false; ui.input.focus();
  }

  async function open() {
    ui.panel.classList.add('open'); ui.launcher.classList.add('open');
    ui.launcher.setAttribute('aria-expanded', 'true');
    if (!info) {
      try {
        const token = await ensureCsrf();
        const r = await fetch('/api/assistant/info', {headers: headers({'X-CSRF-Token': token}), credentials: 'same-origin'});
        info = r.ok ? await r.json() : {greeting: 'Hi! How can I help you find your way around?', suggestions: []};
      } catch { info = {greeting: 'Hi! How can I help you find your way around?', suggestions: []}; }
      ui.badge.textContent = info.ai ? 'AI assistant' : 'Quick guide';
      if (!log.length) addBubble('bot', info.greeting);
      chips();
    }
    setTimeout(() => ui.input.focus(), 80);
  }
  const close = () => { ui.panel.classList.remove('open'); ui.launcher.classList.remove('open'); ui.launcher.setAttribute('aria-expanded', 'false'); ui.launcher.focus(); };

  function build() {
    const wrap = el('div', 'hh-wrap');
    const launcher = el('button', 'hh-launcher'); launcher.type = 'button';
    launcher.setAttribute('aria-label', 'Open help chat'); launcher.setAttribute('aria-expanded', 'false'); launcher.innerHTML = ICON_CHAT + '<span>Help</span>';
    const panel = el('section', 'hh-panel'); panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', 'Haven Help chat');
    const head = el('header', 'hh-head');
    const title = el('div', 'hh-title'); title.append(el('strong', '', 'Haven Help'));
    const badge = el('small', '', 'Ask me where to find things'); title.append(badge);
    const closeBtn = el('button', 'hh-close'); closeBtn.type = 'button'; closeBtn.setAttribute('aria-label', 'Close help chat'); closeBtn.innerHTML = ICON_X;
    head.append(title, closeBtn);
    const list = el('div', 'hh-list'); list.setAttribute('aria-live', 'polite');
    const chipsBox = el('div', 'hh-chips');
    const form = el('form', 'hh-form');
    const input = el('input'); input.type = 'text'; input.maxLength = 600; input.placeholder = 'Ask a question…'; input.setAttribute('aria-label', 'Your question'); input.autocomplete = 'off';
    const sendBtn = el('button', 'hh-send'); sendBtn.type = 'submit'; sendBtn.setAttribute('aria-label', 'Send'); sendBtn.innerHTML = ICON_SEND;
    form.append(input, sendBtn);
    const note = el('p', 'hh-note', 'Never share passwords here. I cannot see your account data.');
    panel.append(head, list, chipsBox, form, note);
    wrap.append(panel, launcher);
    document.body.append(wrap);
    ui = {wrap, launcher, panel, list, chips: chipsBox, input, send: sendBtn, badge};
    launcher.addEventListener('click', () => panel.classList.contains('open') ? close() : open());
    closeBtn.addEventListener('click', close);
    form.addEventListener('submit', ev => { ev.preventDefault(); send(); });
    panel.addEventListener('keydown', ev => { if (ev.key === 'Escape') close(); });
    log.forEach(m => addBubble(m.role, m.text));
  }

  // Show the widget only on signed-in screens (the ones that have a sidebar).
  function sync() {
    const signedIn = !!document.querySelector('.sidebar');
    if (!ui) { if (!signedIn) return; build(); }
    ui.wrap.hidden = !signedIn;
    if (!signedIn) { csrf = ''; info = null; log = []; ui.list.textContent = ''; ui.panel.classList.remove('open'); ui.launcher.classList.remove('open'); }
  }
  let queued = false;
  new MutationObserver(() => { if (queued) return; queued = true; requestAnimationFrame(() => { queued = false; sync(); }); }).observe(document.body, {childList: true, subtree: true});
  sync();
})();
