'use strict';
/* Paged, searchable lists for the big tables (invoices, ledger, audit trail).
   The server sends one page at a time, so screens stay fast with hundreds of tenants. Rows that are loaded
   are also merged into state.data so the existing detail dialogs (Manage, receipts) keep working.
   Months outside the recent window are fetched when you open them on the Overview. */
(() => {
  if (typeof renderPage !== 'function' || typeof render !== 'function') return;
  const SIZES = [25, 50, 100];
  const fmt = n => new Intl.NumberFormat('en-GB').format(n);
  const SPECS = {
    invoices: {
      title: 'Rental invoices', sub: 'Issued invoices retain the original rent and tenant details. Search all of them, any month.', endpoint: '/api/invoices', merge: true,
      placeholder: 'Search invoice number, tenant or property', filters: [['status', 'Status', ['All', 'Unpaid', 'Partially paid', 'Overdue', 'Paid']], ['email', 'Email', ['All', 'Drafts', 'Sent', 'Queued', 'Failed', 'Not emailed']], ['period', 'Month', 'month']],
      head: ['Invoice', 'Tenant / property', 'Amount', 'Outstanding', 'Due', 'Status', 'Email', ''],
      row: i => [`<strong>${e(i.number)}</strong><small>${e(i.period)}</small>`, `<strong>${e(i.tenant_name)}</strong><small>${e(i.property_name)}</small>`, `<span class="amount">${money(i.total_cents)}</span>`,
        money(i.balance_cents), dateLabel(i.due_date), badge(i.status), emailCell(i), `<div class="actions"><a class="button small" href="/api/invoices/${i.id}/pdf">View PDF</a>${actionBtn('Manage', 'invoice-detail', i.id)}</div>`],
      replace: ['Rental invoices', 'Invoice credit adjustments']
    },
    ledger: {
      title: 'Tenant ledger', sub: 'Charges are positive; verified payments are negative. Newest first.', endpoint: '/api/ledger', placeholder: 'Search tenant, invoice or note',
      filters: [['kind', 'Type', 'kinds']], head: ['Entry', 'Recorded', 'Tenant', 'Type', 'Amount', 'Note'],
      row: l => [`#${l.id}`, dateLabel(l.created_at), e(l.tenant_name || '—'), badge(l.kind, 'gray'), money(l.amount_cents), e((l.note || '').slice(0, 70))],
      replace: ['Tenant ledger', 'Audit trail']
    },
    audit: {
      title: 'Audit trail', sub: 'Every recorded action for this company. Search by person, action or record.', endpoint: '/api/audit', placeholder: 'Search person, action or record number',
      filters: [], head: ['When', 'Who', 'Action', 'Record', 'Hash'],
      row: a => [e(a.created_at.replace('T', ' ').slice(0, 19)), e(a.user_name || 'Tenant'), e(a.action.replaceAll('_', ' ')), `${e(a.entity)} #${a.entity_id ?? '—'}`, `<small>${e(a.hash.slice(0, 20))}…</small>`],
      replace: ['Audit trail', null]
    }
  };
  // Email column: what happened to the invoice email, with the action that fits
  const emailCell = i => {
    const st = i.email_status, act = (label, cls = 'small') => `<button type="button" class="button ${cls}" data-pg-email="${i.id}">${e(label)}</button>`;
    if (!i.released_at) return `<div class="pg-email">${badge('Draft', 'amber')}<small>Not sent to the tenant yet</small><button type="button" class="button small primary" data-pg-release="${i.id}">Send to tenant</button></div>`;
    if (st === 'sent') return `<div class="pg-email">${badge('Sent', 'green')}<small title="${e(i.email_to || '')}">${e(dateLabel(i.email_sent_at))}</small>${act('Resend')}</div>`;
    if (st === 'queued' || st === 'sending') return `<div class="pg-email">${badge('Queued', 'amber')}<small>Sending soon</small></div>`;
    if (st === 'failed') return `<div class="pg-email">${badge('Failed', 'red')}<small title="${e(i.email_error || '')}">${e((i.email_error || 'Could not send').slice(0, 40))}</small>${act('Retry')}</div>`;
    return `<div class="pg-email">${badge('Not emailed', 'gray')}<small>${e(i.email_note || '')}</small>${i.email_note && i.email_note === 'Not sent yet' ? act('Send email') : ''}</div>`;
  };
  const PAGER = {};
  const st = id => PAGER[id] ||= {page: 1, size: 50, q: '', f: {}, seq: 0, html: '', foot: '', kinds: []};

  const panelStart = title => `<section class="panel"><div class="panel-header"><div><h2>${title}</h2>`;
  function swapPanel(html, id) {
    const spec = SPECS[id], a = html.indexOf(panelStart(spec.replace[0]));
    if (a < 0) return html;
    const b = spec.replace[1] ? html.indexOf(panelStart(spec.replace[1])) : html.lastIndexOf('</section>') + '</section>'.length;
    return b < 0 ? html : html.slice(0, a) + shell(id) + html.slice(spec.replace[1] ? b : b);
  }
  function shell(id) {
    const spec = SPECS[id], s = st(id);
    const filters = spec.filters.map(([key, label, kind]) => {
      if (kind === 'month') return `<label class="pg-f">${e(label)}<input type="month" data-pg-f="${key}" value="${e(s.f[key] || '')}"></label>`;
      const options = Array.isArray(kind) ? kind : ['All', ...s.kinds];
      return `<label class="pg-f">${e(label)}<select data-pg-f="${key}">${options.map(o => `<option ${((s.f[key] || 'All') === o) ? 'selected' : ''}>${e(o)}</option>`).join('')}</select></label>`;
    }).join('');
    return `<section class="panel pager" data-pager="${id}"><div class="panel-header"><div><h2>${e(spec.title)}</h2><p>${e(spec.sub)}</p></div></div>
      <div class="pg-tools"><label class="ib-search">${icon('search')}<input type="search" data-pg-q placeholder="${e(spec.placeholder)}" value="${e(s.q)}" autocomplete="off"></label>${filters}<span class="pg-count" aria-live="polite"></span></div>
      <div class="pg-body">${s.html || '<div class="pg-loading">Loading…</div>'}</div><div class="pg-foot">${s.foot}</div></section>`;
  }

  function upsert(list, items) {
    const index = new Map(list.map((x, i) => [x.id, i]));
    for (const item of items || []) { if (index.has(item.id)) list[index.get(item.id)] = item; else list.push(item); }
  }
  function mergeRecords(r) {
    const d = state.data;
    if (r.invoices) upsert(d.invoices ||= [], r.invoices);
    if (r.items && r.items.length && r.items[0].number && r.items[0].period) upsert(d.invoices ||= [], r.items.map(({tenant_name, property_name, balance_cents, status, ...rest}) => rest));
    upsert(d.ledger ||= [], r.ledger); upsert(d.payment_proofs ||= [], r.payment_proofs);
  }

  function paint(box, id, r) {
    const spec = SPECS[id], s = st(id);
    if (r.kinds) s.kinds = r.kinds;
    s.html = r.items.length ? table(spec.head, r.items.map(spec.row)) : `<div class="empty"><h3>Nothing found</h3><p>${s.q || Object.values(s.f).some(v => v && v !== 'All') ? 'Try a different search or clear the filters.' : 'There are no records yet.'}</p></div>`;
    const from = r.total ? (r.page - 1) * r.page_size + 1 : 0, to = Math.min(r.total, r.page * r.page_size);
    s.foot = `<span>Showing <strong>${fmt(from)}–${fmt(to)}</strong> of <strong>${fmt(r.total)}</strong></span><span class="pg-nav"><label>Rows <select data-pg-size>${SIZES.map(n => `<option ${n === s.size ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
      <button type="button" class="button small" data-pg-go="${r.page - 1}" ${r.page <= 1 ? 'disabled' : ''}>‹ Previous</button><span class="pg-page">Page ${fmt(r.page)} of ${fmt(r.pages)}</span><button type="button" class="button small" data-pg-go="${r.page + 1}" ${r.page >= r.pages ? 'disabled' : ''}>Next ›</button></span>`;
    box.querySelector('.pg-body').innerHTML = s.html;
    box.querySelector('.pg-foot').innerHTML = s.foot;
    box.querySelector('.pg-count').textContent = `${fmt(r.total)} result${r.total === 1 ? '' : 's'}`;
    if (spec.filters.some(f => f[2] === 'kinds')) box.querySelectorAll('select[data-pg-f="kind"]').forEach(sel => { if (sel.options.length <= 1 && s.kinds.length) sel.innerHTML = ['All', ...s.kinds].map(o => `<option ${(s.f.kind || 'All') === o ? 'selected' : ''}>${e(o)}</option>`).join(''); });
    box.classList.remove('loading');
  }
  async function load(id) {
    const box = document.querySelector(`[data-pager="${id}"]`);
    if (!box) return;
    const spec = SPECS[id], s = st(id), seq = ++s.seq;
    const params = new URLSearchParams({company_id: state.company, page: s.page, page_size: s.size, q: s.q});
    for (const [k, v] of Object.entries(s.f)) if (v && v !== 'All') params.set(k, v);
    box.classList.add('loading');
    try {
      const r = await api(`${spec.endpoint}?${params}`);
      if (seq !== s.seq) return;
      if (spec.merge) mergeRecords(r);
      paint(box, id, r);
    } catch (err) {
      box.classList.remove('loading');
      box.querySelector('.pg-body').innerHTML = `<div class="empty"><h3>Could not load this list</h3><p>${e(err.message)}</p></div>`;
    }
  }

  /* ---------- hook into the page ---------- */
  const baseRenderPage = renderPage;
  renderPage = () => {
    const html = baseRenderPage();
    if (!state.company) return html;
    if (state.page === 'invoices' && ['admin', 'manager', 'finance'].includes(company().role)) return swapPanel(html, 'invoices');
    if (state.page === 'reports' && ['admin', 'manager'].includes(company().role)) return swapPanel(swapPanel(html, 'ledger'), 'audit');
    return html;
  };

  const hydrated = new WeakMap();
  function ensurePeriod(baseRender) {
    const p = state.overviewPeriod, w = state.data && state.data.invoice_window;
    if (state.page !== 'overview' || !p || !w || p >= w.from) return;
    const done = hydrated.get(state.data) || new Set();
    if (done.has(p)) return;
    done.add(p); hydrated.set(state.data, done);
    api(`/api/periods/${p}?company_id=${state.company}`).then(r => { mergeRecords(r); baseRender(); }).catch(() => done.delete(p));
  }
  const baseRender = render;
  render = () => {
    baseRender();
    document.querySelectorAll('[data-pager]').forEach(box => load(box.dataset.pager));
    ensurePeriod(baseRender);
  };

  /* ---------- interaction ---------- */
  const boxOf = el => el.closest('[data-pager]');
  let timer;
  document.addEventListener('input', ev => {
    const q = ev.target.closest && ev.target.closest('[data-pg-q]');
    if (!q) return;
    const box = boxOf(q), id = box.dataset.pager, s = st(id);
    clearTimeout(timer);
    timer = setTimeout(() => { s.q = q.value.trim(); s.page = 1; load(id); }, 280);
  });
  document.addEventListener('change', ev => {
    const el = ev.target;
    if (!el.closest || !boxOf(el)) return;
    const id = boxOf(el).dataset.pager, s = st(id);
    if (el.matches('[data-pg-f]')) { s.f[el.dataset.pgF] = el.value; s.page = 1; load(id); }
    else if (el.matches('[data-pg-size]')) { s.size = Number(el.value); s.page = 1; load(id); }
  });
  document.addEventListener('click', ev => {
    const b = ev.target.closest && ev.target.closest('[data-pg-go]');
    if (!b || b.disabled) return;
    const id = boxOf(b).dataset.pager;
    st(id).page = Math.max(1, Number(b.dataset.pgGo));
    load(id);
    boxOf(b).scrollIntoView({block: 'start', behavior: 'smooth'});
  });
  // Send a draft invoice to the tenant
  document.addEventListener('click', async ev => {
    const btn = ev.target.closest && ev.target.closest('[data-pg-release]');
    if (!btn) return;
    ev.preventDefault(); btn.disabled = true;
    try { await api(`/api/invoices/${btn.dataset.pgRelease}/release`, {method: 'POST', body: {}}); toast('Invoice sent to the tenant.'); } catch (err) { toast(err.message); }
    await refresh();
  });
  // Send, resend or retry an invoice email from the Email column
  document.addEventListener('click', async ev => {
    const btn = ev.target.closest && ev.target.closest('[data-pg-email]');
    if (!btn) return;
    ev.preventDefault(); btn.disabled = true;
    try {
      const r = await api(`/api/invoices/${btn.dataset.pgEmail}/email`, {method: 'POST', body: {}});
      toast(r.result && r.result.sent ? 'Email sent to the tenant.' : r.result && r.result.failed ? 'The email could not be sent. Check the SMTP settings.' : 'Email queued. It will be sent shortly.');
    } catch (err) { toast(err.message); }
    await refresh();
  });
  // After a record changes (refresh), the lists should show the current data.
  const baseRefresh = typeof refresh === 'function' ? refresh : null;
  if (baseRefresh) refresh = async (...args) => { Object.values(PAGER).forEach(s => { s.html = ''; s.foot = ''; }); return baseRefresh(...args); };
})();
