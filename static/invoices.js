'use strict';
/* Invoices as a document you edit in place: every field on the invoice has a pencil, a click turns it into an input,
   and a bar at the bottom saves the changes with a reason (every revision is kept). Also the company logo.
   Plugs into app.js without editing it. */
(() => {
  if (typeof showInvoice !== 'function' || typeof modal !== 'function') return;
  const canEdit = () => ['admin', 'manager', 'finance'].includes(company().role);
  const canBrand = () => ['admin', 'manager'].includes(company().role);
  const logoUrl = c => c && c.logo_version ? `/api/companies/${c.id}/logo?v=${encodeURIComponent(c.logo_version)}` : '';
  const brandMark = (c, cls = '') => {
    const url = logoUrl(c);
    return url ? `<img class="inv-logo ${cls}" src="${e(url)}" alt="${e(c.name)} logo">` : `<span class="inv-logo-fallback ${cls}" aria-hidden="true">${e(initials(c ? c.name : 'H'))}</span>`;
  };
  const cents = v => Math.round(Number(v || 0) * 100);
  const eur = c => money(c);
  const eurosOf = c => (c / 100).toFixed(2);

  // ---------- the editable document
  let doc = null;   // {id, d (server detail), draft, editing}
  const fresh = d => ({
    fields: {...d.fields}, basic: eurosOf(d.current.basic_cents), service: eurosOf(d.current.service_cents), due: d.current.due_date, note: d.current.note || '',
    items: d.current.items.map(x => ({description: x.description, amount: eurosOf(x.amount_cents)}))
  });
  const dirtyList = () => {
    const d = doc.d, x = doc.draft, out = [];
    for (const k of Object.keys(x.fields)) if (x.fields[k] !== d.fields[k]) out.push(k);
    if (cents(x.basic) !== d.current.basic_cents) out.push('basic');
    if (cents(x.service) !== d.current.service_cents) out.push('service');
    if (x.due !== d.current.due_date) out.push('due');
    if (x.note !== (d.current.note || '')) out.push('note');
    if (JSON.stringify(x.items.map(i => [i.description, cents(i.amount)])) !== JSON.stringify(d.current.items.map(i => [i.description, i.amount_cents]))) out.push('lines');
    return out;
  };
  const draftTotal = () => cents(doc.draft.basic) + cents(doc.draft.service) + doc.draft.items.reduce((s, i) => s + cents(i.amount), 0);
  const editable = () => doc.d.editable && canEdit();

  // one field: shows the value with a pencil; while editing it is an input
  function field(key, value, opts = {}) {
    const {kind = 'text', place = 'Add…', multiline = false, show = value, fieldGroup = true} = opts;
    const id = `ef-${key}`;
    if (doc.editing === key) {
      const attr = `id="${id}" data-ef-input="${key}" data-group="${fieldGroup ? 'fields' : ''}" aria-label="${e(opts.label || key)}"`;
      return multiline ? `<textarea ${attr} rows="3">${e(value)}</textarea>`
        : `<input ${attr} type="${kind}" ${kind === 'number' ? 'step="0.01"' : ''} value="${e(value)}" placeholder="${e(place)}">`;
    }
    const shown = String(show || '').trim();
    const body = shown ? `<span class="ef-val">${e(shown).replace(/\n/g, '<br>')}</span>` : `<span class="ef-val empty">${e(place)}</span>`;
    return editable() ? `<span class="ef">${body}<button type="button" class="ef-pen" data-inv="pen" data-key="${key}" aria-label="Edit ${e(opts.label || key)}">${icon('edit')}</button></span>` : `<span class="ef">${body}</span>`;
  }
  const dateShow = v => v ? dateLabel(v) : '';

  function docHtml() {
    const {d, draft: x} = doc, c = company(), url = logoUrl(c), f = x.fields;
    const total = draftTotal(), paid = Math.max(0, d.current.total_cents - d.balance_cents), outstanding = total - paid;
    const status = invStatus(byId('invoices', d.id));
    const pen = (key, label) => editable() ? `<button type="button" class="ef-pen floating" data-inv="pen" data-key="${key}" aria-label="${e(label)}">${icon('edit')}</button>` : '';
    const row = (label, html) => `<div class="inv-kv"><span>${e(label)}</span><span class="inv-colon">:</span><div>${html}</div></div>`;
    const lines = [
      `<tr><td>${field('basic_label', f.basic_label, {label: 'rent line description'})}</td><td>${field('basic', x.basic, {kind: 'number', fieldGroup: false, show: eur(cents(x.basic)), label: 'basic rent amount'})}</td><td></td></tr>`,
      `<tr><td>${field('service_label', f.service_label, {label: 'service line description'})}</td><td>${field('service', x.service, {kind: 'number', fieldGroup: false, show: eur(cents(x.service)), label: 'service charge amount'})}</td><td></td></tr>`,
      ...x.items.map((it, n) => `<tr><td>${doc.editing === `ld-${n}` ? `<input id="ef-ld-${n}" data-ef-input="ld-${n}" data-group="line-desc" data-n="${n}" type="text" maxlength="80" value="${e(it.description)}" placeholder="Description">` : field(`ld-${n}`, it.description, {fieldGroup: false, place: 'Description', label: 'line description'})}</td>
        <td>${doc.editing === `la-${n}` ? `<input id="ef-la-${n}" data-ef-input="la-${n}" data-group="line-amount" data-n="${n}" type="number" step="0.01" value="${e(it.amount)}">` : field(`la-${n}`, it.amount, {fieldGroup: false, kind: 'number', show: it.amount === '' ? '' : eur(cents(it.amount)), place: '0.00', label: 'line amount'})}</td>
        <td>${editable() ? `<button type="button" class="icon-button ef-del" data-inv="line-remove" data-n="${n}" aria-label="Remove line">${icon('close')}</button>` : ''}</td></tr>`)
    ].join('');
    const edits = dirtyList();
    const bar = editable() && edits.length ? `<div class="inv-savebar" role="region" aria-label="Save changes"><div class="inv-savebar-info"><strong>${edits.length} change${edits.length === 1 ? '' : 's'}</strong><span>New total ${eur(total)}${total !== d.current.total_cents ? ` (${total > d.current.total_cents ? '+' : '−'} ${eur(Math.abs(total - d.current.total_cents))})` : ''}</span></div>
      <input id="inv-reason" type="text" maxlength="300" placeholder="Reason for this change (required, kept in the history)" value="${e(doc.reason || '')}" aria-label="Reason for this change">
      <button type="button" class="button" data-inv="discard">Discard</button><button type="button" class="button primary" data-inv="save">Save changes</button></div>` : '';
    const rev = d.revisions.length ? `<details class="inv-history"><summary>Revision history <span>${d.revisions.length}</span></summary><ul>${d.revisions.map(r =>
      `<li><strong>Revision ${r.revision}</strong> · ${e(dateLabel(r.created_at))} · ${e(r.editor)}<br><span>${e(r.reason)}</span><br><small>${eur(r.before.total_cents)} → ${eur(r.after.total_cents)}</small></li>`).join('')}</ul></details>` : '';
    const lock = canEdit() && !d.editable ? `<div class="inv-lockbar">${icon('lock')}<span><strong>Locked.</strong> ${e(d.edit_block || '')}</span></div>` : '';
    return `<div class="inv-sheet inv-doc" id="inv-doc">
      ${d.released ? '' : `<div class="inv-draftbar"><span>${icon('edit')} <strong>Draft.</strong> The tenant has not received this invoice yet. Check it, change anything with the pencils, then send it.</span>${canEdit() ? `<button type="button" class="button primary" data-inv="send" data-id="${d.id}">Send to tenant</button>` : ''}</div>`}
      <div class="inv-docbar"><span class="inv-number">${e(d.number)}</span>${d.released ? badge(status) : badge('Draft', 'amber')}${editable() ? '<span class="inv-hint">Click a pencil to edit that part of the invoice.</span>' : ''}</div>
      <header class="inv-top"><div class="inv-logo-wrap">${url ? `<img class="inv-logo big" src="${e(url)}" alt="${e(c.name)} logo">` : `<span class="inv-logo-fallback big">${e(initials(f.company_name || c.name))}</span>`}
          ${canBrand() ? `<label class="ef-pen floating" tabindex="0" aria-label="Change the logo">${icon('edit')}<input type="file" id="logo-file" accept="image/png,image/jpeg" hidden></label>` : ''}</div>
        <div class="inv-from"><strong>${field('company_name', f.company_name, {label: 'company name'})}</strong>${field('company_address', f.company_address, {multiline: true, place: 'Company address', label: 'company address'})}</div></header>
      <section class="inv-to">${field('tenant_name', f.tenant_name, {label: 'recipient name'})}${field('address', f.address, {multiline: true, place: 'Recipient address', label: 'recipient address'})}</section>
      <section class="inv-kvs">
        ${row('Invoice date', field('invoice_date', f.invoice_date, {kind: 'date', show: dateShow(f.invoice_date), label: 'invoice date'}))}
        ${row('Invoice number', `<span class="ef"><span class="ef-val">${e(d.number)}</span><span class="ef-lock" title="The invoice number never changes">${icon('lock')}</span></span>`)}
        ${row('Due date', field('due', x.due, {kind: 'date', fieldGroup: false, show: dateShow(x.due), label: 'due date'}))}
        ${row('Property', field('object', f.object, {place: 'Property', label: 'property'}))}
        ${row('Reference', field('reference', f.reference, {place: 'Add a reference', label: 'reference'}))}
        ${row('Period', `${field('from', f.from, {kind: 'date', show: dateShow(f.from), label: 'period start'})} <span class="inv-dash">–</span> ${field('to', f.to, {kind: 'date', show: dateShow(f.to), label: 'period end'})}`)}
      </section>
      <table class="inv-lines"><thead><tr><th>Description</th><th>Amount</th><th></th></tr></thead><tbody>${lines}</tbody>
        <tfoot>${editable() && x.items.length < 10 ? '<tr class="inv-add"><td colspan="3"><button type="button" class="button small" data-inv="line-add">' + icon('plus') + ' Add a line</button></td></tr>' : ''}
        <tr class="inv-total"><td>Total</td><td>${eur(total)}</td><td></td></tr>${paid ? `<tr><td>Paid</td><td>− ${eur(paid)}</td><td></td></tr>` : ''}<tr class="inv-due"><td>Outstanding</td><td>${eur(Math.max(0, outstanding))}</td><td></td></tr></tfoot></table>
      <div class="inv-notebox"><label>Note on the invoice</label>${field('note', x.note, {multiline: true, fieldGroup: false, place: 'Add a note for the tenant', label: 'note'})}</div>
      ${d.payment_instructions ? `<div class="inv-pay"><label>How to pay</label><p>${e(d.payment_instructions)}</p></div>` : ''}
      ${lock}${rev}
      <div class="inv-actions"><a class="button" href="/api/invoices/${d.id}/pdf">${icon('file')} View PDF</a>${actionBtn('Record receipt', 'staff-proof', d.id)}${actionBtn('Propose credit', 'credit-form', d.id)}${actionBtn('Create tenant link', 'create-link', d.id, 'primary')}${actionBtn('Revoke link', 'revoke-link', d.id)}</div>
      ${bar}</div>`;
  }
  function redrawDoc(focusKey) {
    const host = document.querySelector('#modal .modal-body');
    if (!host || !doc) return;
    const scroll = host.scrollTop;
    host.innerHTML = docHtml();
    host.scrollTop = scroll;
    if (focusKey) { const el = document.getElementById(`ef-${focusKey}`); if (el) { el.focus(); if (el.select && el.tagName === 'INPUT' && el.type !== 'date') el.select(); } }
  }
  async function openInvoice(id) {
    let d;
    try { d = await api(`/api/invoices/${id}/detail`); } catch (err) { toast(err.message); return; }
    doc = {id, d, draft: fresh(d), editing: null, reason: ''};
    modal(d.number, docHtml());
    const dlg = document.getElementById('modal');
    dlg.classList.add('doc-modal');
    dlg.addEventListener('close', () => { dlg.classList.remove('doc-modal'); doc = null; }, {once: true});
  }
  showInvoice = id => { openInvoice(id); };

  // commit what is typed in the field that is being edited
  function commit(input) {
    if (!doc || !input) return;
    const key = input.dataset.efInput, v = input.value, x = doc.draft;
    if (input.dataset.group === 'fields') x.fields[key] = v.trim();
    else if (key === 'basic') x.basic = v === '' ? '0' : v;
    else if (key === 'service') x.service = v === '' ? '0' : v;
    else if (key === 'due') x.due = v || x.due;
    else if (key === 'note') x.note = v.trim();
    else if (key.startsWith('ld-')) x.items[Number(input.dataset.n)].description = v;
    else if (key.startsWith('la-')) x.items[Number(input.dataset.n)].amount = v;
    doc.editing = null;
  }

  // ---------- company logo in Settings and on the invoices page
  function logoPanel() {
    const c = company(), url = logoUrl(c);
    return panel('Company logo', 'Shown at the top of every invoice, in the invoice preview and in the PDF.',
      `<div class="panel-body logo-panel"><div class="logo-stage">${url ? `<img class="inv-logo big" src="${e(url)}" alt="${e(c.name)} logo">` : `<span class="inv-logo-fallback big">${e(initials(c.name))}</span><small>No logo yet. Initials are shown instead.</small>`}</div>
        <div class="logo-actions">${canBrand() ? `<label class="button primary">${icon('upload')} ${url ? 'Replace logo' : 'Upload logo'}<input type="file" id="logo-file" accept="image/png,image/jpeg" hidden></label>${url ? '<button type="button" class="button" data-inv="logo-remove">Remove</button>' : ''}` : '<small>Ask a manager to upload the logo.</small>'}
        <small>PNG or JPEG, up to 1 MB. A wide logo on a white or transparent background looks best.</small></div></div>`);
  }
  function reviewPanel() {
    const c = company(), on = !!c.review_invoices;
    return panel('Review invoices before sending', 'When on, "Run monthly invoices" creates drafts. Nothing reaches the tenant (portal, alert or email) until you open the invoice, check or edit it and press Send to tenant.',
      `<div class="panel-body"><label class="rv-switch"><input type="checkbox" ${on ? 'checked' : ''} ${canBrand() ? '' : 'disabled'} data-inv-change="review-toggle"> <span>${on ? 'On: new invoices wait for your review' : 'Off: new invoices are sent to tenants automatically'}</span></label></div>`);
  }
  const baseRender = renderPage;
  renderPage = () => {
    const html = baseRender();
    if (!state.company || !state.me) return html;
    if (state.page === 'settings') return html.replace('<div class="section-stack">', () => '<div class="section-stack">' + reviewPanel() + logoPanel());
    if (state.page === 'invoices') {
      const c = company();
      const drafts = rows('invoices').filter(i => !i.released_at).length;
      const draftBanner = drafts ? `<div class="notice inv-drafts"><span>${icon('edit')} <strong>${drafts} draft invoice${drafts === 1 ? '' : 's'} waiting for your review.</strong> Open one with Manage to check or edit it.</span>${canEdit() ? '<button type="button" class="button primary small" data-inv="send-all">Send all to tenants</button>' : ''}</div>` : '';
      return html.replace(/(<div class="page-heading">)/, m => draftBanner + `<div class="inv-strip">${brandMark(c)}<div><strong>${e(c.name)}</strong><small>${e(c.address || '')}</small></div>${canBrand() && !c.logo_version ? '<button type="button" class="button small" data-action="navigate" data-page="settings">Add your logo</button>' : ''}</div>` + m);
    }
    return html;
  };

  // ---------- events
  const actions = {
    pen: el => { if (!doc) return; if (doc.editing) commit(document.querySelector('[data-ef-input]')); doc.editing = el.dataset.key; redrawDoc(el.dataset.key); },
    'line-add': () => { doc.draft.items.push({description: '', amount: ''}); doc.editing = `ld-${doc.draft.items.length - 1}`; redrawDoc(doc.editing); },
    'line-remove': el => { doc.draft.items.splice(Number(el.dataset.n), 1); doc.editing = null; redrawDoc(); },
    send: async el => {
      if (doc && dirtyList().length) { toast('Save or discard your changes first, then send the invoice.'); return; }
      await api(`/api/invoices/${el.dataset.id}/release`, {method: 'POST', body: {}});
      const id = Number(el.dataset.id);
      await refresh(); toast('Invoice sent to the tenant.'); await openInvoice(id);
    },
    'send-all': async () => {
      const n = rows('invoices').filter(i => !i.released_at).length;
      if (!confirm(`Send all ${n} draft invoice${n === 1 ? '' : 's'} to the tenants now?`)) return;
      const r = await api('/api/invoices/release', {method: 'POST', body: {company_id: state.company}});
      await refresh(); toast(`${r.released} invoice${r.released === 1 ? '' : 's'} sent to the tenants.`);
    },
    'review-toggle': async el => {
      await api(`/api/companies/${company().id}/invoice-review`, {method: 'POST', body: {enabled: el.checked}});
      state.me = await api('/api/me'); render();
      toast(el.checked ? 'New invoices will wait as drafts until you send them.' : 'New invoices will be sent to tenants automatically.');
    },
    discard: () => { doc.draft = fresh(doc.d); doc.editing = null; doc.reason = ''; redrawDoc(); },
    save: async () => {
      if (doc.editing) commit(document.querySelector('[data-ef-input]'));
      const reason = (document.getElementById('inv-reason') || {}).value || doc.reason || '';
      doc.reason = reason;
      if (!reason.trim()) { redrawDoc(); document.getElementById('inv-reason')?.focus(); toast('Add a short reason for the change first.'); return; }
      const x = doc.draft, changed = {};
      for (const k of Object.keys(x.fields)) if (x.fields[k] !== doc.d.fields[k]) changed[k] = x.fields[k];
      const items = x.items.filter(i => i.description.trim() || i.amount !== '');
      await api(`/api/invoices/${doc.id}/edit`, {method: 'POST', body: {basic_rent: x.basic, service_charge: x.service, due_date: x.due, note: x.note, reason, items, fields: changed}});
      const id = doc.id;
      await refresh(); toast('Invoice updated. The tenant was notified.');
      await openInvoice(id);
    },
    'logo-remove': async () => {
      if (!confirm('Remove the company logo?')) return;
      await api(`/api/companies/${company().id}/logo`, {method: 'DELETE'});
      state.me = await api('/api/me'); render(); toast('Logo removed.');
    }
  };
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-inv]');
    if (!el || !actions[el.dataset.inv]) return;
    ev.preventDefault();
    Promise.resolve(actions[el.dataset.inv](el)).catch(err => toast(err.message));
  });
  // leaving a field keeps what was typed in the draft
  document.addEventListener('focusout', ev => {
    const input = ev.target.closest && ev.target.closest('[data-ef-input]');
    if (!input || !doc) return;
    setTimeout(() => { if (doc && doc.editing === input.dataset.efInput && !document.activeElement?.closest('[data-ef-input]')) { commit(input); redrawDoc(); } }, 0);
  });
  document.addEventListener('keydown', ev => {
    const input = ev.target.closest && ev.target.closest('[data-ef-input]');
    if (!input || !doc) return;
    if (ev.key === 'Enter' && input.tagName !== 'TEXTAREA') { ev.preventDefault(); commit(input); redrawDoc(); }
    else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); doc.editing = null; redrawDoc(); }
  }, true);
  document.addEventListener('input', ev => { if (ev.target.id === 'inv-reason' && doc) doc.reason = ev.target.value; });
  document.addEventListener('change', ev => { const el = ev.target.closest && ev.target.closest('[data-inv-change]'); if (el && actions[el.dataset.invChange]) Promise.resolve(actions[el.dataset.invChange](el)).catch(err => { toast(err.message); render(); }); });
  document.addEventListener('change', async ev => {
    if (ev.target.id !== 'logo-file' || !ev.target.files[0]) return;
    const file = ev.target.files[0];
    if (file.size > 1024 * 1024) { toast('The logo must be 1 MB or smaller.'); ev.target.value = ''; return; }
    const fd = new FormData(); fd.append('file', file);
    try {
      await api(`/api/companies/${company().id}/logo`, {method: 'POST', body: fd});
      state.me = await api('/api/me');
      if (doc) redrawDoc(); else render();
      toast('Logo saved. It now appears on your invoices.');
    } catch (err) { toast(err.message); }
  });
})();
