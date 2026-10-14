'use strict';
/* Owner portal: portfolio overview, units, open rent and monthly statements (read only). */
(() => {
  if (location.pathname !== '/owner') return;
  const root = document.getElementById('app');
  const S = {me: null, data: null, csrf: '', page: 'overview'};
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const cash = n => new Intl.NumberFormat('en-NL', {style: 'currency', currency: 'EUR'}).format((n || 0) / 100);
  const day = v => v ? new Intl.DateTimeFormat('en-GB', {day: 'numeric', month: 'short', year: 'numeric'}).format(new Date(v.slice(0, 10) + 'T12:00:00')) : '—';
  const month = p => new Intl.DateTimeFormat('en-GB', {month: 'long', year: 'numeric'}).format(new Date(p + '-01T12:00:00'));
  const ico = {
    home: 'M2 10l10-8 10 8 M5 9v13h14V9 M9 22v-9h6v9', building: 'M4 21V5l8-3v19 M12 8h8v13 M2 21h20 M7 7v1 M7 11v1 M7 15v1 M16 11h1 M16 15h1',
    file: 'M14 2H5v20h14V7z M14 2v6h5 M8 12h8 M8 16h6', folder: 'M2 6h7l2 3h11v12H2z M2 6V3h7l2 3h9v3', down: 'M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5', logout: 'M9 3H3v18h6 M9 12h12 M17 8l4 4-4 4',
    check: 'M5 12l4 4L19 6', clock: 'M12 8v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0', wallet: 'M20 8H5a3 3 0 0 1 0-6h13v6 M3 5v14a2 2 0 0 0 2 2h15V8 M20 12h-5v5h5',
  };
  const ic = k => '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="' + (ico[k] || ico.home) + '"/></svg>';
  const tone = {paid: 'green', published: 'blue'};
  const badge = s => '<span class="badge ' + (tone[s] || 'gray') + '">' + esc(s[0].toUpperCase() + s.slice(1)) + '</span>';

  async function call(url, opt = {}) {
    const h = {...(opt.headers || {}), 'X-Haven-Portal': 'owner'};
    if (S.csrf) h['X-CSRF-Token'] = S.csrf;
    if (opt.body && !(opt.body instanceof FormData)) { h['Content-Type'] = 'application/json'; opt.body = JSON.stringify(opt.body); }
    const r = await fetch(url, {...opt, headers: h});
    const d = await r.json().catch(() => ({detail: 'Unexpected server response.'}));
    if (!r.ok) throw new Error(Array.isArray(d.detail) ? d.detail.map(x => x.msg).join('; ') : d.detail || 'Request failed.');
    return d;
  }

  function login(message = 'Sign in to see your portfolio.') {
    root.innerHTML = `<main class="public-page"><header class="public-header"><a href="/" class="brand"><span class="brand-mark">h</span>haven</a><small>OWNER ACCESS</small></header>
      <section class="panel"><div class="eyebrow">YOUR PROPERTIES, TRANSPARENTLY</div><h1>Owner portal</h1><p class="subtitle">${esc(message)}</p>
      <form id="owner-login"><div class="field"><label for="email">Email</label><input id="email" name="email" type="email" autocomplete="username" required></div>
      <div class="field"><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required></div>
      <p class="form-error" role="alert"></p><button class="button primary">Sign in</button></form></section></main>`;
    document.getElementById('owner-login').onsubmit = async ev => {
      ev.preventDefault(); const b = ev.currentTarget.querySelector('button'); b.disabled = true;
      try { const d = await call('/api/login', {method: 'POST', body: Object.fromEntries(new FormData(ev.currentTarget))}); S.csrf = d.csrf; await start(); }
      catch (err) { ev.currentTarget.querySelector('.form-error').textContent = err.message; b.disabled = false; }
    };
  }

  const stat = (label, value, foot) => `<div class="stat"><div class="stat-top"><span>${esc(label)}</span></div><div class="stat-value">${value}</div><div class="stat-foot">${esc(foot || '')}</div></div>`;
  const table = (heads, body, none) => body.length ? `<div class="table-wrap"><table><thead><tr>${heads.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${body.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : `<div class="empty"><h3>${esc(none)}</h3></div>`;
  const panel = (t, s, b) => `<section class="panel"><div class="panel-header"><div><h2>${esc(t)}</h2>${s ? `<p>${esc(s)}</p>` : ''}</div></div>${b}</section>`;
  const pdfLink = id => `<a class="button small" href="/api/owner/statements/${id}/pdf" target="_blank" rel="noopener">${ic('down')} PDF</a>`;

  function overview(d) {
    const occupied = d.units.filter(u => u.occupied).length;
    const pct = d.units.length ? Math.round(occupied / d.units.length * 100) : 0;
    const latest = d.statements[0];
    const roll = d.units.reduce((s, u) => s + (u.rent_cents || 0), 0);
    const open = d.arrears.reduce((s, a) => s + a.open_cents, 0);
    return `<div class="page-heading"><div><div class="eyebrow">PORTFOLIO</div><h1>Welcome, ${esc(d.accounts[0]?.name || S.me.name)}</h1><p class="subtitle">${d.portfolios.map(p => esc(p.name)).join(' · ') || 'No portfolio linked yet.'}</p></div></div>
      <div class="summary-grid">${stat('Units', d.units.length, occupied + ' occupied')}${stat('Occupancy', pct + '%', d.units.length - occupied + ' vacant')}${stat('Monthly rent roll', cash(roll), 'Rent + service advance')}${stat('Collected this month', cash(d.month_collected_cents), 'Verified payments')}</div>
      <div class="summary-grid">${stat('Paid out this year', cash(d.paid_ytd_cents), 'Net of fee and expenses')}${stat('Open rent', cash(open), d.arrears.length + ' overdue invoice' + (d.arrears.length === 1 ? '' : 's'))}${latest ? stat('Latest statement', cash(latest.net_cents), month(latest.period) + ' · ' + latest.status) : stat('Latest statement', '—', 'None published yet')}</div>`
      + (d.arrears.length ? panel('Open rent', 'Overdue invoices your manager is following up', table(['Unit', 'Invoice', 'Due', 'Open'], d.arrears.map(a => [esc(a.property), esc(a.number), day(a.due_date), cash(a.open_cents)]), '')) : `<div class="notice">${ic('check')}<span>All rent due so far has been paid. Nice and quiet.</span></div>`)
      + panel('Recent statements', '', table(['Month', 'Net', 'Status', ''], d.statements.slice(0, 3).map(s => [esc(month(s.period)), `<strong>${cash(s.net_cents)}</strong>`, badge(s.status), pdfLink(s.id)]), 'No statements yet'));
  }
  function units(d) {
    return `<div class="page-heading"><div><div class="eyebrow">PORTFOLIO</div><h1>Your units</h1><p class="subtitle">Current occupancy and rent for every unit.</p></div></div>`
      + panel('Units', '', table(['Unit', 'Address', 'Tenant', 'Rent / month', 'Lease ends', 'Energy label'], d.units.map(u => [`<strong>${esc(u.name)}</strong>`, esc(u.address + ', ' + u.city), u.occupied ? esc(u.tenant) : '<span class="badge amber">Vacant</span>', u.rent_cents ? cash(u.rent_cents) : '—', u.lease_end ? day(u.lease_end) : (u.occupied ? 'Open-ended' : '—'), esc(u.energy_label)]), 'No units linked yet'));
  }
  function statements(d) {
    return `<div class="page-heading"><div><div class="eyebrow">FINANCE</div><h1>Monthly statements</h1><p class="subtitle">Rent collected, the management fee and expenses, month by month. Download the PDF for your records.</p></div></div>`
      + panel('Statements', '', table(['Month', 'Portfolio', 'Collected', 'Fee', 'Expenses', 'Net to you', 'Status', ''], d.statements.map(s => [`<strong>${esc(month(s.period))}</strong>`, esc(s.portfolio), cash(s.collected_cents), '−' + cash(s.fee_cents), '−' + cash(s.expenses_cents), `<strong>${cash(s.net_cents)}</strong>`, badge(s.status) + (s.paid_on ? `<small>${esc(day(s.paid_on))}</small>` : ''), pdfLink(s.id)]), 'No statements published yet'));
  }

  const fmtSize = n => n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';
  function documents(d) {
    const list = d.documents.map(x => [`<strong>${esc(x.title)}</strong>`, esc(x.category), x.from_owner ? '<span class="badge blue">You</span>' : '<span class="badge gray">Your manager</span>', day(x.shared_at), fmtSize(x.size), `<a class="button small" href="/api/owner/documents/${x.id}/file" target="_blank" rel="noopener">${ic('down')} Open</a>`]);
    return `<div class="page-heading"><div><div class="eyebrow">DOCUMENTS</div><h1>Your documents</h1><p class="subtitle">Insurance papers, deeds and valuations, shared in both directions.</p></div></div>`
      + panel('Documents', '', table(['Document', 'Category', 'From', 'Added', 'Size', ''], list, 'Nothing shared yet'))
      + `<section class="panel"><div class="panel-header"><div><h2>Send a document to your manager</h2><p>PDF, PNG or JPEG up to 10 MB.</p></div></div><form id="owner-upload" class="panel-body"><div class="form-grid"><div class="field"><label for="cat">Category</label><select id="cat" name="category">${d.doc_categories.map(c => `<option>${esc(c)}</option>`).join('')}</select></div><div class="field"><label for="ttl">Title (optional)</label><input id="ttl" name="title" maxlength="150"></div><div class="field wide"><label for="fl">File</label><input id="fl" name="file" type="file" accept="application/pdf,image/png,image/jpeg" required></div></div><p class="form-error" role="alert"></p><button class="button primary">${ic('down')} Upload</button></form></section>`;
  }

  const nav = [['overview', 'Overview', 'home'], ['units', 'Units', 'building'], ['statements', 'Statements', 'file'], ['documents', 'Documents', 'folder']];
  function shell() {
    const d = S.data;
    const unread = (d.notifications || []).filter(n => !n.is_read).length;
    const page = S.page === 'units' ? units(d) : S.page === 'statements' ? statements(d) : S.page === 'documents' ? documents(d) : overview(d);
    root.innerHTML = `<div class="portal-shell"><aside class="sidebar"><div class="brand"><span class="brand-mark">h</span><div>haven<small>OWNER PORTAL</small></div></div>
      <div class="portal-identity"><span class="avatar">${esc((S.me.name || '?').split(' ').map(x => x[0]).slice(0, 2).join('').toUpperCase())}</span><strong>${esc(S.me.name)}</strong><small>${esc(d.accounts[0]?.company_name || '')}</small></div>
      <nav>${nav.map(([id, label, i]) => `<a href="#${id}" class="nav-item ${S.page === id ? 'active' : ''}" data-nav="${id}">${ic(i)}<span>${label}</span></a>`).join('')}</nav>
      <div class="sidebar-footer"><button class="button" data-do="logout">${ic('logout')} Sign out</button></div></aside>
      <div class="workspace"><header class="topbar"><div class="breadcrumb">Owner portal / <span>${esc(nav.find(n => n[0] === S.page)[1])}</span></div><div class="topbar-separator"></div><span class="date-pill">${esc(day(d.today))}</span></header>
      <main id="portal-main">${unread && S.page === 'overview' ? d.notifications.filter(n => !n.is_read).slice(0, 2).map(n => `<div class="notice"><span><strong>${esc(n.title)}</strong><br>${esc(n.body)}</span><button class="button small" data-do="read" data-id="${n.id}">Dismiss</button></div>`).join('') : ''}${page}</main></div></div>`;
    document.querySelectorAll('[data-width]').forEach(el => { el.style.width = el.dataset.width + '%'; });
    const up = document.getElementById('owner-upload');
    if (up) up.onsubmit = async ev => {
      ev.preventDefault(); const b = up.querySelector('button'); b.disabled = true;
      try { await call('/api/owner/documents', {method: 'POST', body: new FormData(up)}); S.data = await call('/api/owner/data'); shell(); }
      catch (err) { up.querySelector('.form-error').textContent = err.message; b.disabled = false; }
    };
  }
  document.addEventListener('click', async ev => {
    const nv = ev.target.closest('[data-nav]');
    if (nv && S.data) { ev.preventDefault(); S.page = nv.dataset.nav; history.replaceState(null, '', '#' + S.page); shell(); window.scrollTo(0, 0); return; }
    const b = ev.target.closest('[data-do]'); if (!b) return;
    try {
      if (b.dataset.do === 'logout') { await call('/api/logout', {method: 'POST', body: {}}); S.me = null; S.csrf = ''; login('You are signed out.'); }
      if (b.dataset.do === 'read') { await call('/api/notifications/' + b.dataset.id + '/read', {method: 'POST', body: {}}); S.data = await call('/api/owner/data'); shell(); }
    } catch (err) { alert(err.message); }
  });
  async function start() {
    try {
      S.me = await call('/api/me'); S.csrf = S.me.csrf;
      if (!S.me.owner_access) { login('This account does not have owner access. Contact your property manager.'); return; }
      S.data = await call('/api/owner/data');
      S.page = nav.some(n => n[0] === location.hash.slice(1)) ? location.hash.slice(1) : 'overview';
      shell();
    } catch (err) { login(err.message.includes('sign in') ? undefined : err.message); }
  }
  start();
})();
