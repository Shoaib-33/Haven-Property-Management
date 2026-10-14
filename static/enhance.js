'use strict';
/* Haven UI enhancement layer: command palette, smart insights, sortable/filterable tables,
   keyboard shortcuts and live data refresh. Loaded after app.js and works on top of its state. */
(() => {
  const ready = () => typeof state !== 'undefined' && typeof navGroups !== 'undefined' && !['/tenant', '/contractor', '/owner'].includes(location.pathname) && state.me && document.querySelector('.sidebar') && document.getElementById('main');
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const norm = v => String(v ?? '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  const store = {
    get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
  };
  const go = page => {
    const m = document.getElementById('modal'); if (m?.open) m.close();
    state.page = page; state.filter = 'All';
    history.replaceState(null, '', '#' + page);
    render(); window.scrollTo(0, 0);
  };
  const todayStr = () => state.data?.today || new Date().toLocaleDateString('en-CA');
  const daysBetween = (a, b) => Math.round((new Date(b + 'T12:00:00') - new Date(a + 'T12:00:00')) / 864e5);

  /* ---------- Smart insights ---------- */
  function insights() {
    const out = [];
    const t = todayStr();
    const invoices = rows('invoices');
    const overdue = invoices.filter(i => i.due_date < t && balance(i) > 0);
    if (overdue.length) {
      const sum = overdue.reduce((s, i) => s + balance(i), 0);
      const oldest = Math.max(...overdue.map(i => daysBetween(i.due_date, t)));
      out.push({tone: oldest > 30 ? 'red' : 'amber', weight: 90 + Math.min(oldest, 60) / 10, icon: 'clock', page: 'arrears',
        title: `${money(sum)} overdue across ${overdue.length} invoice${overdue.length > 1 ? 's' : ''}`,
        copy: `The oldest is ${oldest} day${oldest === 1 ? '' : 's'} late. Open arrears to follow up.`, cta: 'Review arrears'});
    }
    const pending = rows('payment_proofs').filter(p => p.status === 'pending');
    if (pending.length) out.push({tone: 'amber', weight: 80, icon: 'shield', page: 'payments',
      title: `${pending.length} payment receipt${pending.length > 1 ? 's' : ''} waiting`,
      copy: 'Verifying quickly keeps tenant balances accurate.', cta: 'Verify now'});
    const signedOffers = rows('lease_offers').filter(o => o.status === 'signed');
    if (signedOffers.length) out.push({tone: 'amber', weight: 96, icon: 'file', page: 'tenants',
      title: `${signedOffers.length} signed contract${signedOffers.length > 1 ? 's' : ''} waiting for approval`,
      copy: `${signedOffers[0].tenant_name} signed. Approve it so the tenancy can start.`, cta: 'Review contracts'});
    const lateRepairs = rows('repairs').filter(r => !['done', 'closed'].includes(r.status) && r.due_date < t);
    if (lateRepairs.length) out.push({tone: 'red', weight: 85, icon: 'wrench', page: 'repairs',
      title: `${lateRepairs.length} repair${lateRepairs.length > 1 ? 's are' : ' is'} past the response deadline`,
      copy: 'Dutch tenancy rules expect timely handling of reported defects.', cta: 'Open maintenance'});
    const pol = u => (rows('sla_policies').find(p => p.urgency === u) || {response_hours: {Emergency: 4, Urgent: 48}[u] || 120}).response_hours;
    const unanswered = rows('repairs').filter(r => !['done', 'closed'].includes(r.status) && !r.responded_at && (Date.parse(r.response_due_at || '') || Date.parse(r.created_at) + pol(r.urgency) * 36e5) < Date.now());
    if (unanswered.length) out.push({tone: 'red', weight: 92, icon: 'wrench', page: 'repairs',
      title: `${unanswered.length} repair${unanswered.length > 1 ? 's' : ''} still waiting for a first response`,
      copy: `The response deadline has passed for "${unanswered[0].title}".`, cta: 'Respond now'});
    const emergencies = rows('repairs').filter(r => r.urgency === 'Emergency' && !['done', 'closed'].includes(r.status));
    if (emergencies.length) out.push({tone: 'red', weight: 95, icon: 'wrench', page: 'repairs',
      title: `${emergencies.length} open emergency repair${emergencies.length > 1 ? 's' : ''}`,
      copy: emergencies[0].title, cta: 'Respond'});
    const ending = rows('contracts').filter(c => c.end_date && c.end_date >= t && daysBetween(t, c.end_date) <= 60 && c.status !== 'ended');
    if (ending.length) out.push({tone: 'blue', weight: 60, icon: 'calendar', page: 'tenants',
      title: `${ending.length} tenanc${ending.length > 1 ? 'ies end' : 'y ends'} within 60 days`,
      copy: 'Plan move-out inspections and deposit returns ahead of time.', cta: 'See tenancies'});
    const vacant = rows('properties').filter(p => !activeContract(p.id) && !upcomingContract(p.id));
    if (vacant.length && rows('properties').length) out.push({tone: 'blue', weight: 40, icon: 'building', page: 'properties',
      title: `${vacant.length} vacant unit${vacant.length > 1 ? 's' : ''}`,
      copy: `${Math.round((1 - vacant.length / rows('properties').length) * 100)}% of your units are occupied.`, cta: 'View properties'});
    const dueTasks = rows('tasks').filter(x => x.status !== 'done' && x.due_date <= t);
    if (dueTasks.length) out.push({tone: 'amber', weight: 55, icon: 'check', page: 'tasks',
      title: `${dueTasks.length} task${dueTasks.length > 1 ? 's' : ''} due today or earlier`,
      copy: dueTasks[0].title, cta: 'Open tasks'});
    const cur = t.slice(0, 7), billed = invoices.filter(i => i.period === cur);
    if (billed.length) {
      const total = billed.reduce((s, i) => s + i.total_cents, 0);
      const paid = billed.reduce((s, i) => s + Math.max(0, i.total_cents - Math.max(0, balance(i))), 0);
      const pct = total ? Math.round(paid / total * 100) : 0;
      out.push({tone: pct >= 90 ? 'green' : 'blue', weight: pct >= 90 ? 20 : 45, icon: 'chart', page: 'invoices',
        title: `${pct}% of this month's rent is collected`, copy: `${money(paid)} of ${money(total)} billed.`, cta: 'View invoices'});
    }
    return out.sort((a, b) => b.weight - a.weight);
  }

  function renderInsights() {
    const main = document.getElementById('main');
    if (!main || state.page !== 'overview' || !state.company || document.getElementById('insights')) return;
    const heading = main.querySelector('.page-heading');
    if (!heading) return;
    const list = insights();
    const body = list.length ? list.slice(0, 5).map((it, i) => `
      <button type="button" class="insight ${it.tone}" data-insight="${esc(it.page)}">
        <span class="insight-icon">${icon(it.icon)}</span>
        <span class="insight-text"><strong>${esc(it.title)}</strong><small>${esc(it.copy)}</small></span>
        <span class="insight-cta">${esc(it.cta)} ${icon('arrow')}</span>
      </button>`).join('') : `<div class="insight green"><span class="insight-icon">${icon('check')}</span><span class="insight-text"><strong>Everything is on track</strong><small>No overdue rent, late repairs or waiting receipts.</small></span></div>`;
    const el = document.createElement('section');
    el.id = 'insights'; el.className = 'panel insights-panel';
    el.innerHTML = `<div class="panel-header"><div><h2>Smart insights</h2><p>Haven checked your company. Here is what deserves attention first.</p></div><button type="button" class="button small" data-open-palette>${icon('search')} Quick actions <kbd>Ctrl K</kbd></button></div><div class="insight-list">${body}</div>`;
    (main.querySelector('.period-bar') || heading).after(el);
    el.querySelectorAll('.insight').forEach((n, i) => n.style.setProperty('--i', i));
  }

  /* ---------- Tables: sorting, filtering, export ---------- */
  const cellValue = td => {
    const raw = (td?.dataset.sortValue ?? td?.textContent ?? '').trim();
    if (/^[−\-+]?\s*[€$]?\s*[\d.,\s]+$/.test(raw) && /\d/.test(raw)) {
      const n = parseFloat(raw.replace(/[€$\s]/g, '').replace(/\.(?=\d{3}\b)/g, '').replace(',', '.').replace('−', '-'));
      if (!isNaN(n)) return n;
    }
    const d = Date.parse(raw);
    if (!isNaN(d) && /\d{4}/.test(raw) && raw.length < 24) return d;
    return norm(raw);
  };
  function enhanceTable(wrap) {
    const table = wrap.querySelector('table');
    if (!table || wrap.dataset.enh) return;
    wrap.dataset.enh = '1';
    const body = table.tBodies[0];
    if (!body) return;
    const heads = [...table.tHead.rows[0].cells];
    heads.forEach((th, idx) => {
      if (!th.textContent.trim()) return;
      th.classList.add('sortable'); th.tabIndex = 0; th.setAttribute('role', 'columnheader');
      const sort = () => {
        const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
        heads.forEach(h => { delete h.dataset.dir; h.removeAttribute('aria-sort'); });
        th.dataset.dir = dir; th.setAttribute('aria-sort', dir === 'asc' ? 'ascending' : 'descending');
        const trs = [...body.rows];
        trs.sort((a, b) => {
          const x = cellValue(a.cells[idx]), y = cellValue(b.cells[idx]);
          const r = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, {numeric: true});
          return dir === 'asc' ? r : -r;
        });
        trs.forEach(tr => body.appendChild(tr));
      };
      th.addEventListener('click', sort);
      th.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); sort(); } });
    });
    if (body.rows.length < 6) return;
    const bar = document.createElement('div');
    bar.className = 'table-tools';
    bar.innerHTML = `<label class="table-filter">${icon('search')}<input type="search" placeholder="Filter ${body.rows.length} rows…" aria-label="Filter table rows"></label><span class="table-count" aria-live="polite"></span><button type="button" class="button small" data-export>${icon('down')} CSV</button>`;
    wrap.before(bar);
    const input = bar.querySelector('input'), count = bar.querySelector('.table-count');
    const apply = () => {
      const q = norm(input.value).split(/\s+/).filter(Boolean);
      let shown = 0;
      [...body.rows].forEach(tr => {
        const text = norm(tr.textContent);
        const ok = q.every(w => text.includes(w));
        tr.hidden = !ok; if (ok) shown++;
      });
      count.textContent = q.length ? `${shown} of ${body.rows.length}` : '';
      let none = wrap.querySelector('.no-match');
      if (!shown && !none) { none = document.createElement('div'); none.className = 'no-match'; none.textContent = 'No rows match your filter.'; wrap.appendChild(none); }
      if (shown && none) none.remove();
    };
    input.addEventListener('input', apply);
    bar.querySelector('[data-export]').addEventListener('click', () => {
      const cell = c => '"' + c.textContent.replace(/\s+/g, ' ').trim().replace(/"/g, '""').replace(/^([=+\-@])/, "'$1") + '"';
      const lines = [[...table.tHead.rows[0].cells].map(cell).join(',')];
      [...body.rows].filter(tr => !tr.hidden).forEach(tr => lines.push([...tr.cells].map(cell).join(',')));
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob(['﻿' + lines.join('\r\n')], {type: 'text/csv'}));
      a.download = `haven-${state.page}.csv`; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      toast(`Exported ${lines.length - 1} rows.`);
    });
  }

  /* ---------- Command palette ---------- */
  let palette, activeIndex = 0, results = [];
  const pages = () => navGroups.flatMap(g => g[1].map(([id, label, ic]) => ({id, label, ic, group: g[0]}))).filter(p => allowedPage(p.id));
  function buildItems(query) {
    const q = norm(query).trim();
    const recents = store.get('haven_recent', []);
    const items = [];
    const match = text => !q || q.split(/\s+/).every(w => norm(text).includes(w));
    pages().forEach(p => { if (match(p.label + ' ' + p.group + ' go open')) items.push({kind: 'Page', ic: p.ic, title: p.label, sub: p.group.toLowerCase(), run: () => go(p.id), key: 'p:' + p.id}); });
    const role = company().role;
    const quick = [
      ['New property', 'building', 'Add a property to the portfolio', () => { go('properties'); document.querySelector('[data-action="form"][data-kind="properties"]')?.click(); }, ['admin', 'manager', 'property']],
      ['New tenant', 'users', 'Add a tenant contact', () => { go('tenants'); document.querySelector('[data-action="form"][data-kind="tenants"]')?.click(); }, ['admin', 'manager', 'property']],
      ['Log a repair', 'wrench', 'Record a maintenance request', () => { go('repairs'); document.querySelector('[data-action="form"][data-kind="repairs"]')?.click(); }, ['admin', 'manager', 'property']],
      ['Refresh data', 'clock', 'Reload the latest records', () => refreshNow(true), null],
    ];
    quick.forEach(([title, ic, sub, run, roles]) => { if ((!roles || roles.includes(role)) && match(title + ' ' + sub + ' new add create')) items.push({kind: 'Action', ic, title, sub, run, key: 'a:' + title}); });
    if (q.length >= 2) {
      const add = (kind, ic, list, title, sub, page) => list.forEach(r => { const t = title(r), s = sub(r); if (match(t + ' ' + s)) items.push({kind, ic, title: t, sub: s, run: () => { go(page); setTimeout(() => { const i = document.querySelector('.table-tools input'); if (i) { i.value = t; i.dispatchEvent(new Event('input')); } }, 60); }, key: kind + r.id}); });
      if (pages().some(p => p.id === 'properties')) add('Property', 'building', rows('properties'), r => r.name, r => `${r.address}, ${r.city}`, 'properties');
      if (pages().some(p => p.id === 'tenants')) add('Tenant', 'users', rows('tenants'), r => r.name, r => r.email, 'tenants');
      if (pages().some(p => p.id === 'invoices')) add('Invoice', 'file', rows('invoices'), r => r.number, r => `${invStatus(r)} · ${money(r.total_cents)}`, 'invoices');
      if (pages().some(p => p.id === 'repairs')) add('Repair', 'wrench', rows('repairs'), r => r.title, r => `${r.urgency} · ${r.status}`, 'repairs');
    }
    if (!q) items.sort((a, b) => (recents.indexOf(b.key) + 1) - (recents.indexOf(a.key) + 1));
    return items.slice(0, 40);
  }
  function openPalette() {
    if (!ready()) return;
    if (!palette) {
      palette = document.createElement('dialog');
      palette.id = 'palette'; palette.setAttribute('aria-label', 'Command palette');
      palette.innerHTML = `<div class="pal-box"><div class="pal-input">${icon('search')}<input type="text" placeholder="Search pages, tenants, invoices, or type an action…" aria-label="Command palette" autocomplete="off" spellcheck="false"><kbd>Esc</kbd></div><ul class="pal-list" role="listbox"></ul><div class="pal-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>g</kbd> then <kbd>o</kbd>/<kbd>p</kbd>/<kbd>t</kbd>/<kbd>i</kbd>/<kbd>m</kbd> jump</span></div></div>`;
      document.body.appendChild(palette);
      const input = palette.querySelector('input');
      input.addEventListener('input', () => { activeIndex = 0; draw(input.value); });
      input.addEventListener('keydown', ev => {
        if (ev.key === 'ArrowDown') { ev.preventDefault(); activeIndex = Math.min(results.length - 1, activeIndex + 1); mark(); }
        else if (ev.key === 'ArrowUp') { ev.preventDefault(); activeIndex = Math.max(0, activeIndex - 1); mark(); }
        else if (ev.key === 'Enter') { ev.preventDefault(); choose(activeIndex); }
      });
      palette.addEventListener('click', ev => { if (ev.target === palette) palette.close(); const li = ev.target.closest('li[data-i]'); if (li) choose(Number(li.dataset.i)); });
      palette.addEventListener('mousemove', ev => { const li = ev.target.closest('li[data-i]'); if (li && Number(li.dataset.i) !== activeIndex) { activeIndex = Number(li.dataset.i); mark(); } });
    }
    const input = palette.querySelector('input'); input.value = ''; activeIndex = 0; draw('');
    if (!palette.open) palette.showModal();
    input.focus();
  }
  function draw(q) {
    results = buildItems(q);
    const list = palette.querySelector('.pal-list');
    list.innerHTML = results.length ? results.map((r, i) => `<li role="option" data-i="${i}" class="${i === activeIndex ? 'active' : ''}"><span class="pal-ic">${icon(r.ic)}</span><span class="pal-t"><strong>${esc(r.title)}</strong><small>${esc(r.sub)}</small></span><span class="pal-kind">${esc(r.kind)}</span></li>`).join('') : `<li class="pal-empty">Nothing matches “${esc(q)}”.</li>`;
  }
  function mark() {
    palette.querySelectorAll('li[data-i]').forEach(li => li.classList.toggle('active', Number(li.dataset.i) === activeIndex));
    palette.querySelector('li.active')?.scrollIntoView({block: 'nearest'});
  }
  function choose(i) {
    const item = results[i]; if (!item) return;
    const recents = [item.key, ...store.get('haven_recent', []).filter(k => k !== item.key)].slice(0, 8);
    store.set('haven_recent', recents);
    palette.close();
    item.run();
  }

  /* ---------- Shortcuts ---------- */
  const typing = el => el && (el.matches?.('input,textarea,select,[contenteditable]') || el.isContentEditable);
  let chord = 0;
  const jumps = {o: 'overview', p: 'properties', t: 'tenants', i: 'invoices', m: 'repairs', d: 'documents', a: 'arrears', k: 'tasks'};
  document.addEventListener('keydown', ev => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'k' && ready()) {
      ev.preventDefault(); ev.stopImmediatePropagation(); openPalette(); return;
    }
    if (!ready() || typing(ev.target) || ev.ctrlKey || ev.metaKey || ev.altKey || document.querySelector('dialog[open]')) return;
    if (ev.key === '/') { ev.preventDefault(); openPalette(); return; }
    if (ev.key === 'g') { chord = Date.now(); return; }
    if (chord && Date.now() - chord < 1200 && jumps[ev.key]) {
      chord = 0;
      if (allowedPage(jumps[ev.key])) go(jumps[ev.key]);
    }
  }, true);
  document.addEventListener('click', ev => {
    const ins = ev.target.closest('[data-insight]');
    if (ins) { go(ins.dataset.insight); return; }
    if (ev.target.closest('[data-open-palette]')) openPalette();
  });

  /* ---------- Live refresh ---------- */
  let lastSnapshot = null, lastUpdated = Date.now(), refreshing = false;
  async function refreshNow(manual) {
    if (refreshing || !ready() || !state.company) return;
    refreshing = true;
    try {
      const fresh = await api(`/api/data?company_id=${state.company}`);
      const snap = JSON.stringify(fresh, (k, v) => k === 'today' ? undefined : v);
      lastUpdated = Date.now();
      if (snap !== lastSnapshot) {
        lastSnapshot = snap;
        state.data = fresh;
        if (!document.querySelector('dialog[open]') && !typing(document.activeElement)) {
          const y = window.scrollY; render(); window.scrollTo(0, y);
        }
        if (manual) toast('Up to date.');
      } else if (manual) toast('Already up to date.');
    } catch (err) { if (manual) toast(err.message); }
    finally { refreshing = false; updateStamp(); }
  }
  setInterval(() => {
    if (document.visibilityState === 'visible' && !document.querySelector('dialog[open]') && !typing(document.activeElement)) refreshNow(false);
  }, 60000);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && Date.now() - lastUpdated > 30000) refreshNow(false); });
  window.addEventListener('offline', () => toast('You are offline. Changes cannot be saved until the connection returns.'));
  window.addEventListener('online', () => { toast('Back online.'); refreshNow(false); });
  function updateStamp() {
    const el = document.getElementById('live-stamp');
    if (el) el.textContent = 'Updated ' + new Date(lastUpdated).toLocaleTimeString('en-GB', {hour: '2-digit', minute: '2-digit'});
  }

  /* ---------- Topbar extras & modal niceties ---------- */
  function decorateTopbar() {
    const bar = document.querySelector('.topbar');
    if (!bar || bar.dataset.enh) return;
    bar.dataset.enh = '1';
    const wrap = document.createElement('span');
    wrap.className = 'live-tools';
    wrap.innerHTML = `<button type="button" class="icon-button" data-open-palette aria-label="Open quick actions (Ctrl K)" title="Quick actions (Ctrl K)">${icon('plus')}</button><button type="button" class="icon-button" id="live-refresh" aria-label="Refresh data" title="Refresh data">${icon('clock')}</button><small id="live-stamp"></small>`;
    bar.querySelector('.topbar-separator')?.before(wrap);
    wrap.querySelector('#live-refresh').addEventListener('click', () => refreshNow(true));
    updateStamp();
  }
  const modal = document.getElementById('modal');
  if (modal) {
    new MutationObserver(() => {
      if (!modal.open) return;
      const first = modal.querySelector('input:not([type=hidden]):not([disabled]),select,textarea');
      if (first && !modal.contains(document.activeElement)) first.focus();
    }).observe(modal, {childList: true, subtree: true});
    modal.addEventListener('keydown', ev => {
      if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') modal.querySelector('form')?.requestSubmit();
    });
  }

  /* ---------- Orchestration ---------- */
  let lastPage = null, scheduled = false, observer;
  function enhance() {
    scheduled = false;
    if (!ready()) return;
    observer.disconnect();
    try {
      decorateTopbar();
      const main = document.getElementById('main');
      if (state.page !== lastPage) {
        lastPage = state.page;
        main.classList.add('page-enter');
        setTimeout(() => main.classList.remove('page-enter'), 700);
      }
      renderInsights();
      main.querySelectorAll('.table-wrap').forEach(enhanceTable);
      if (lastSnapshot === null) lastSnapshot = JSON.stringify(state.data, (k, v) => k === 'today' ? undefined : v);
    } finally { observer.observe(document.getElementById('app'), {childList: true, subtree: true}); }
  }
  observer = new MutationObserver(() => { if (!scheduled) { scheduled = true; requestAnimationFrame(enhance); } });
  observer.observe(document.getElementById('app'), {childList: true, subtree: true});
})();
