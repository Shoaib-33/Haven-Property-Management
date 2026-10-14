'use strict';
/* Landlord side of "send a contract for signature": the offers panel on Tenants & contracts, the send dialog,
   and approve / return / withdraw. The tenant signs in their portal (portal.js). Plugs into app.js without editing it. */
(() => {
  if (typeof renderPage !== 'function' || typeof formModal !== 'function') return;
  const baseRender = renderPage;
  const canSend = () => ['admin', 'manager', 'property'].includes(company().role);
  const LABEL = {sent: ['Waiting for tenant', 'amber'], signed: ['Signed: needs your approval', 'blue'], returned: ['Returned to tenant', 'amber'], approved: ['Active tenancy', 'green'], declined: ['Declined by tenant', 'red'], withdrawn: ['Withdrawn', 'gray']};
  const offers = () => rows('lease_offers').filter(o => o.status !== 'withdrawn');
  const lease = (label, action, id = '', cls = 'small') => `<button type="button" class="button ${cls}" data-lease="${action}" data-id="${id}">${e(label)}</button>`;

  renderPage = () => {
    const html = baseRender();
    if (state.page !== 'tenants' || !state.company || !canSend()) return html;
    const swapped = html.replace(/<button[^>]*data-action="manual-contract-form"[^>]*>[^<]*<\/button>/, () => lease('Send contract for signature', 'send', '', 'primary') + `<button type="button" class="button" data-action="manual-contract-form" title="For a contract that was signed on paper">Signed on paper? Record it</button>`);
    return swapped.replace('<section class="panel">', () => offersPanel() + '<section class="panel">');
  };

  function actionsFor(o) {
    const view = `<a class="button small" href="/api/lease-offers/${o.id}/file" target="_blank" rel="noopener">View PDF</a>`;
    if (o.status === 'signed') return view + lease('Approve', 'approve', o.id, 'small primary') + lease('Return', 'return', o.id);
    if (o.status === 'sent' || o.status === 'returned') return view + lease('Withdraw', 'withdraw', o.id, 'small danger');
    if (o.status === 'approved') return view + `<button type="button" class="button small" data-action="contract-detail" data-id="${o.contract_id}">Open file</button>`;
    return view;
  }
  function offersPanel() {
    const list = offers(), waiting = list.filter(o => o.status === 'signed').length;
    const banner = waiting ? `<div class="notice lease-alert">${icon('check')}<span><strong>${waiting} signed contract${waiting === 1 ? '' : 's'} waiting for your approval.</strong> Approving it creates the tenancy and the tenant joins.</span></div>` : '';
    const body = list.length ? table(['Tenant', 'Property', 'Starts', 'Rent / month', 'Status', ''], list.map(o => [
      `<strong>${e(o.tenant_name)}</strong>${o.response_note ? `<small>${e(o.response_note)}</small>` : ''}`, e(o.property_name), dateLabel(o.start_date),
      `<span class="amount">${money(o.basic_cents + o.service_cents)}</span>`, badge(LABEL[o.status][0], LABEL[o.status][1]), `<div class="actions">${actionsFor(o)}</div>`
    ])) : `<div class="empty"><h3>No contracts out for signature</h3><p>Press <strong>Send contract for signature</strong>. The tenant reads and signs it in their portal, then you approve it.</p></div>`;
    return `${banner}${panel('Contracts for signature', 'Sent to the tenant portal. A contract becomes a tenancy only after you approve the signature.', body)}`;
  }

  const hasLogin = tenantId => rows('portal_accounts').some(a => a.tenant_id === tenantId && a.enabled);
  // What the tenant can be asked to fill in (must match PRESETS in app/lease_offers.py)
  const ASK = [['phone', 'Phone number', 1, 1], ['date_of_birth', 'Date of birth', 1, 1], ['id_type', 'ID document type', 1, 1], ['id_number', 'ID document number', 1, 1], ['current_address', 'Current address', 1, 1],
    ['employer', 'Employer', 0, 0], ['monthly_income', 'Net monthly income (EUR)', 0, 0], ['emergency_name', 'Emergency contact name', 0, 0], ['emergency_phone', 'Emergency contact phone', 0, 0],
    ['house_rules', 'Accepts the house rules (tick box)', 1, 0], ['privacy', 'Agrees to processing of their details (tick box)', 1, 1]];
  let sendDraft = null, hasSig = null;
  const val = (f, n) => f.elements[n] ? f.elements[n].value : '';
  function showSendForm() {
    const d = sendDraft, templates = rows('doc_templates').filter(t => t.kind === 'Lease' && !t.archived), tenants = rows('tenants').filter(t => !t.archived_at);
    const sel = (name, label, list, cur, cls = '') => `<div class="field ${cls}"><label for="ls-${name}">${label}</label><select id="ls-${name}" name="${name}" required>${list.map(([v, l]) => `<option value="${e(v)}" ${String(cur) === String(v) ? 'selected' : ''}>${e(l)}</option>`).join('')}</select></div>`;
    const inp = (name, label, type, cur, extra = '') => `<div class="field"><label for="ls-${name}">${label}</label><input id="ls-${name}" name="${name}" type="${type}" ${extra} value="${e(cur ?? '')}"></div>`;
    const ask = ASK.map(([key, label, , on]) => { const was = (d.asks || {})[key]; const checked = was ? true : d.asks ? false : !!on; return `<div class="lf-field"><input type="checkbox" id="ask-${key}" name="ask-${key}" ${checked ? 'checked' : ''}><label for="ask-${key}">${e(label)}</label><select name="req-${key}" aria-label="Required or optional"><option value="1" ${!was || was.required ? 'selected' : ''}>Required</option><option value="0" ${was && !was.required ? 'selected' : ''}>Optional</option></select></div>`; }).join('');
    modal('Send contract for signature', `<form id="ls-form" class="lf-form">${hasSig === false ? `<div class="notice"><span><strong>Your signature is not set up yet.</strong> The contract will go out without it and you sign when you approve. To put your signature on every contract as it is sent, add it in <a href="#settings" data-action="navigate" data-page="settings">Settings</a>.</span></div>` : hasSig ? `<p class="lf-ok">${icon('check')} Your signature will be placed on the contract when it is sent.</p>` : ''}<p class="modal-note">Step 1 of 2. Haven fills the contract from your template. Next you can read it and edit the wording for this tenant, then send it to their portal.</p>
      <div class="form-grid">${sel('tenant_id', 'Tenant', tenants.map(t => [t.id, t.name + (hasLogin(t.id) ? '' : ' (no portal login yet)')]), d.tenant_id, 'wide')}
      ${sel('property_id', 'Property', rows('properties').map(p => [p.id, p.name + (activeContract(p.id) ? ' (occupied)' : '')]), d.property_id)}${sel('template_id', 'Contract template', templates.map(t => [t.id, t.name]), d.template_id)}
      ${inp('start_date', 'Tenancy starts', 'date', d.start_date || today(), 'required')}${inp('end_date', 'Ends (leave empty if open-ended)', 'date', d.end_date)}
      ${inp('basic_rent', 'Basic rent per month (€)', 'number', d.basic_rent, 'required step="0.01" min="0"')}${inp('service_charge', 'Service-charge advance (€)', 'number', d.service_charge ?? '0', 'step="0.01" min="0"')}${inp('deposit', 'Deposit (€)', 'number', d.deposit ?? '0', 'step="0.01" min="0"')}</div>
      <h3 class="lf-h">Details the tenant fills in before signing</h3><div class="lf-fields">${ask}</div>
      <div class="form-grid"><div class="field"><label for="ls-c1">Your own question (optional)</label><input id="ls-c1" name="custom1" type="text" maxlength="80" value="${e(d.custom1 || '')}" placeholder="e.g. Licence plate number"></div><div class="field"><label for="ls-c2">Another question (optional)</label><input id="ls-c2" name="custom2" type="text" maxlength="80" value="${e(d.custom2 || '')}"></div></div>
      <p class="form-error" id="ls-error" role="alert"></p><div class="modal-footer"><button type="button" class="button" data-action="close-modal">Cancel</button><button type="submit" class="button primary">Next: read and edit the contract</button></div></form>`);
  }
  function collectDraft(f) {
    const d = {tenant_id: Number(val(f, 'tenant_id')), property_id: Number(val(f, 'property_id')), template_id: Number(val(f, 'template_id')), start_date: val(f, 'start_date'), end_date: val(f, 'end_date') || null,
      basic_rent: val(f, 'basic_rent'), service_charge: val(f, 'service_charge') || '0', deposit: val(f, 'deposit') || '0', custom1: val(f, 'custom1').trim(), custom2: val(f, 'custom2').trim(), asks: {}};
    for (const [key] of ASK) if (f.elements['ask-' + key]?.checked) d.asks[key] = {required: val(f, 'req-' + key) !== '0'};
    return d;
  }
  const formFieldsOf = d => [...Object.entries(d.asks).map(([key, v]) => ({key, required: v.required})), ...[d.custom1, d.custom2].filter(Boolean).map(label => ({label, required: true}))];
  const payload = d => ({company_id: state.company, tenant_id: d.tenant_id, property_id: d.property_id, template_id: d.template_id, start_date: d.start_date, end_date: d.end_date, basic_rent: d.basic_rent, service_charge: d.service_charge, deposit: d.deposit});
  // Step 2: the contract as a document, set up like the PDF, edited in place. Open details are inputs inside the text.
  let doc = null, sigInfo = null;
  async function showEditStep(d) {
    let r;
    try { r = await api('/api/lease-offers/preview', {method: 'POST', body: payload(d)}); } catch (err) { document.getElementById('ls-error').textContent = err.message; return; }
    const c = company();
    const asks = [...Object.entries(d.asks).map(([key, v]) => ({label: (ASK.find(a => a[0] === key) || [key, key])[1], required: v.required})), ...[d.custom1, d.custom2].filter(Boolean).map(label => ({label, required: true}))];
    modal('Read and edit the contract', `<div class="de-note">Step 2 of 2. This is how the contract will look to the tenant. Click a pencil to change a sentence. The yellow boxes are details Haven does not know yet. Your template stays as it is.</div>
      <div class="de-wrap"><div id="de-host"></div></div>
      <div class="de-bar"><span class="de-status" id="de-status"></span><p class="form-error" id="ls-error" role="alert"></p><button type="button" class="button" data-ls="back">Back</button><button type="button" class="button primary" id="ls-go">Send to tenant</button></div>`);
    const dlg = document.getElementById('modal');
    dlg.classList.add('doc-modal'); dlg.addEventListener('close', () => { dlg.classList.remove('doc-modal'); doc = null; }, {once: true});
    const status = document.getElementById('de-status'), go = document.getElementById('ls-go');
    const check = () => {
      const left = doc ? doc.missing() : [];
      go.disabled = left.length > 0;
      status.textContent = left.length ? `${left.length} detail${left.length === 1 ? '' : 's'} still to fill in (yellow boxes): ${left.map(k => doc.labelOf(k)).join(', ')}` : 'Ready to send.';
      status.classList.toggle('warn', left.length > 0);
    };
    doc = HavenDoc.mount(document.getElementById('de-host'), {text: r.body, company: {name: c.name, address: c.address, kvk: c.kvk}, logo: c.logo_version ? `/api/companies/${c.id}/logo?v=${encodeURIComponent(c.logo_version)}` : '',
      signature: sigInfo && sigInfo.set ? sigInfo : null, asks, onChange: check});
    check();
    go.addEventListener('click', async () => {
      const err = document.getElementById('ls-error'); go.disabled = true; err.textContent = '';
      try {
        await api('/api/lease-offers', {method: 'POST', body: {...payload(sendDraft), body: doc.text(), form_fields: formFieldsOf(sendDraft)}});
        sendDraft = null; closeModal(); await refresh(); toast('Sent. The tenant can now read, fill in and sign it in their portal.');
      } catch (ex) { err.textContent = ex.message; check(); }
    });
  }
  document.addEventListener('submit', async ev => {
    const f = ev.target;
    if (f.id === 'ls-form') {
      ev.preventDefault(); sendDraft = collectDraft(f);
      const btn = f.querySelector('button[type=submit]'); btn.disabled = true;
      await showEditStep(sendDraft); btn.disabled = false;
    }
  });
  document.addEventListener('click', ev => {
    if (ev.target.closest('[data-ls="back"]')) { ev.preventDefault(); document.getElementById('modal').classList.remove('doc-modal'); doc = null; showSendForm(); }
  });

  // ---------- Settings: the landlord's own signature, placed on every contract when it is sent
  const baseRenderAll = render;
  render = () => { baseRenderAll(); if (state.page === 'settings' && state.company && canSend()) mountSignaturePanel(); };
  const baseRenderPage = renderPage;
  renderPage = () => {
    const html = baseRenderPage();
    if (state.page !== 'settings' || !state.company || !canSend()) return html;
    const panelHtml = panel('Your signature on contracts', 'Set it once. Haven places it on every tenancy agreement when you send it, so the tenant receives a contract you have already signed.',
      '<div class="panel-body" id="sig-panel"><p class="subtitle">Loading…</p></div>');
    return html.replace('<div class="section-stack">', () => '<div class="section-stack">' + panelHtml);
  };
  async function mountSignaturePanel() {
    const host = document.getElementById('sig-panel'); if (!host) return;
    let cur;
    try { cur = await api(`/api/companies/${state.company}/signature`); } catch (err) { host.innerHTML = `<p class="form-error">${e(err.message)}</p>`; return; }
    const manager = ['admin', 'manager'].includes(company().role);
    host.innerHTML = `${cur.set ? `<div class="lf-current"><img class="lf-sigimg" src="${e(cur.signature)}" alt="Your saved signature"><div><strong>${e(cur.name)}</strong><br><span>${e(cur.title || '')}</span><br><small>Saved ${e(dateLabel((cur.updated_at || '').slice(0, 10)))}</small></div>${manager ? '<button type="button" class="button small danger" data-ls="sig-remove">Remove</button>' : ''}</div>` : '<p class="subtitle">No signature yet. Contracts will go out unsigned and you sign when you approve.</p>'}
      ${manager ? `<form id="sig-form" class="lf-sigform"><h3>${cur.set ? 'Replace your signature' : 'Add your signature'}</h3>
        <div class="form-grid"><div class="field"><label for="sg-name">Name as printed under the signature</label><input id="sg-name" name="name" type="text" maxlength="100" required value="${e(cur.name || state.me.name)}"></div>
        <div class="field"><label for="sg-title">Role (optional)</label><input id="sg-title" name="title" type="text" maxlength="100" placeholder="e.g. Director" value="${e(cur.title || '')}"></div></div>
        ${HavenSignature.html(cur.name || state.me.name)}<p class="form-error" id="sg-error" role="alert"></p>
        <div class="modal-footer"><button type="submit" class="button primary" id="sg-save" disabled>Save signature</button></div></form>` : '<p class="subtitle">Only a manager can change the signature.</p>'}`;
    const form = document.getElementById('sig-form'); if (!form) return;
    const save = document.getElementById('sg-save');
    const pad = HavenSignature.bind(form, cur.name || state.me.name, () => { save.disabled = !pad.ready(); });
    requestAnimationFrame(() => pad.refit());
    form.addEventListener('submit', async ev => {
      ev.preventDefault(); save.disabled = true;
      try {
        const sig = await pad.data();
        await api(`/api/companies/${state.company}/signature`, {method: 'POST', body: {name: form.elements.name.value, title: form.elements.title.value, signature: sig.dataUrl, signature_mode: sig.mode}});
        toast('Signature saved. It will be placed on every contract you send.'); mountSignaturePanel();
      } catch (err) { document.getElementById('sg-error').textContent = err.message; save.disabled = false; }
    });
  }
  document.addEventListener('click', async ev => {
    if (!ev.target.closest('[data-ls="sig-remove"]')) return;
    ev.preventDefault();
    if (!confirm('Remove your saved signature? Contracts will go out unsigned until you add it again.')) return;
    try { await api(`/api/companies/${state.company}/signature`, {method: 'DELETE'}); toast('Signature removed.'); mountSignaturePanel(); } catch (err) { toast(err.message); }
  });

  const actions = {
    send() {
      const templates = rows('doc_templates').filter(t => t.kind === 'Lease' && !t.archived);
      if (!templates.length) { toast('Add a Lease template first (Templates & fields).'); state.page = 'templates'; render(); return; }
      if (!rows('tenants').filter(t => !t.archived_at).length) { toast('Add the tenant first.'); return; }
      if (!rows('properties').length) { toast('Add a property first.'); return; }
      sendDraft = sendDraft || {};
      api(`/api/companies/${state.company}/signature`).then(r => { hasSig = !!r.set; sigInfo = r; }).catch(() => { hasSig = null; sigInfo = null; }).then(showSendForm);
    },
    async approve(id) {
      const o = byId('lease_offers', Number(id));
      let r;
      try { r = await api(`/api/lease-offers/${id}/review`); } catch (err) { toast(err.message); return; }
      const answers = r.fields.length ? `<h3>Details the tenant filled in</h3><div class="lf-answers">${r.fields.map(f => `<div><span>${e(f.label)}</span><strong>${e(r.values[f.key] || '—')}</strong></div>`).join('')}</div>` : '';
      const how = {draw: 'drawn', type: 'typed', upload: 'uploaded'}[r.signature_mode];
      const needPad = !r.landlord_signed;
      modal('Review and approve', `<div class="sign-sheet">
        <p>Signed by <strong>${e(r.signer_name || '')}</strong> on ${e(dateLabel((r.signed_at || '').slice(0, 10)))}${how ? ` with a ${how} signature` : ''}. ${r.edited ? 'The contract text was edited for this tenant.' : ''}</p>
        ${r.signature ? `<img class="lf-sigimg" src="${e(r.signature)}" alt="The tenant's signature">` : ''}
        ${answers}
        <p><a class="button small" href="/api/lease-offers/${id}/file" target="_blank" rel="noopener">${icon('folder')} Open the contract as signed so far (PDF)</a></p>
        ${needPad ? `<div class="sign-step"><span>2</span>Sign for the landlord</div>${HavenSignature.html(state.me.name)}` : `<p class="lf-ok">${icon('check')} The contract already carries the landlord's signature (${e(r.landlord_name || '')}), placed when it was sent.</p>`}
        <div class="sign-agree"><input type="checkbox" id="la-confirm"><label for="la-confirm">I checked the signed contract. Approving makes ${e(o.tenant_name)} the tenant of ${e(o.property_name)} from ${e(dateLabel(o.start_date))}.</label></div>
        <p class="form-error" id="la-error" role="alert"></p>
        <div class="modal-footer"><button type="button" class="button" data-action="close-modal">Cancel</button><button type="button" class="button primary" id="la-go" disabled>Approve and create tenancy</button></div></div>`);
      const dlg = document.getElementById('modal'), root = dlg.querySelector('.sign-sheet'), go = document.getElementById('la-go'), box = document.getElementById('la-confirm');
      dlg.classList.add('sign-modal'); dlg.addEventListener('close', () => dlg.classList.remove('sign-modal'), {once: true});
      const pad = needPad ? HavenSignature.bind(root, state.me.name, () => check()) : null;
      const check = () => { go.disabled = !((!pad || pad.ready()) && box.checked); };
      box.addEventListener('change', check);
      if (pad) requestAnimationFrame(() => pad.refit());
      go.addEventListener('click', async () => {
        go.disabled = true; document.getElementById('la-error').textContent = '';
        try {
          const sig = pad ? await pad.data() : null;
          await api(`/api/lease-offers/${id}/approve`, {method: 'POST', body: {confirmed: true, ...(sig ? {signature: sig.dataUrl, signature_mode: sig.mode} : {})}});
          closeModal(); await refresh(); toast('Tenancy created. The signed agreement is in the tenant portal.');
        } catch (err) { document.getElementById('la-error').textContent = err.message; check(); }
      });
    },
    return(id) {
      formModal('Return to tenant', [['note', 'What should the tenant check or change?', 'textarea', {wide: true}]],
        d => api(`/api/lease-offers/${id}/return`, {method: 'POST', body: {note: d.note}}), {submit: 'Return contract', success: 'Returned to the tenant.', intro: 'The signature is cleared and the tenant can read it and sign again.'});
    },
    async withdraw(id) {
      if (!confirm('Withdraw this contract? The tenant will no longer be able to sign it.')) return;
      await api(`/api/lease-offers/${id}/withdraw`, {method: 'POST', body: {}});
      await refresh(); toast('Contract withdrawn.');
    }
  };
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-lease]');
    if (!el || !actions[el.dataset.lease]) return;
    ev.preventDefault();
    Promise.resolve(actions[el.dataset.lease](el.dataset.id)).catch(err => toast(err.message));
  });
})();
