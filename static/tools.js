'use strict';
/* Staff tools: repair SLA tracker, automation rules, document templates and custom fields.
   Plugs into app.js without editing it. */
(() => {
  if (typeof navGroups === 'undefined' || typeof renderPage !== 'function') return;
  navGroups[2][1].push(['automation', 'Automation', 'clock'], ['templates', 'Templates & fields', 'file']);

  const prevAllowed = allowedPage;
  allowedPage = page => {
    const role = company().role;
    if (page === 'automation') return ['admin', 'manager'].includes(role);
    if (page === 'templates') return role !== 'inspector';
    return prevAllowed(page);
  };
  const prevRender = renderPage;
  renderPage = () => {
    if (state.page === 'automation') return automationPage();
    if (state.page === 'templates') return templatesPage();
    const html = prevRender();
    return state.page === 'repairs' ? html.replace('<div class="status-board">', () => slaPanel() + '<div class="status-board">') : html;
  };

  const tool = (label, action, id = '', cls = 'small', extra = '') => `<button type="button" class="button ${cls}" data-tool="${action}" data-id="${id}" ${extra}>${e(label)}</button>`;
  const isManager = () => ['admin', 'manager'].includes(company().role);

  /* ---------- SLA tracker ---------- */
  const DEFAULT_SLA = {Emergency: [4, 2], Urgent: [48, 14], Routine: [120, 42]};
  const slaOf = urgency => { const p = rows('sla_policies').find(x => x.urgency === urgency); return p ? [p.response_hours, p.resolve_days] : DEFAULT_SLA[urgency] || DEFAULT_SLA.Routine; };
  const span = ms => { const a = Math.abs(ms), m = Math.round(a / 6e4); if (m < 60) return `${m}m`; const h = Math.floor(m / 60); if (h < 48) return `${h}h ${m % 60 ? m % 60 + 'm' : ''}`.trim(); return `${Math.round(h / 24)}d`; };
  function slaState(r) {
    const [hours] = slaOf(r.urgency);
    const dueAt = Date.parse(r.response_due_at || '') || (Date.parse(r.created_at) + hours * 36e5);
    const nowMs = Date.now();
    const out = {response: null, resolve: null, breached: 0, risk: 0, score: 0};
    if (r.responded_at) out.response = ['Responded', 'green'];
    else if (nowMs > dueAt) { out.response = [`Late by ${span(nowMs - dueAt)}`, 'red']; out.breached++; out.score += 100 + (nowMs - dueAt) / 36e5; }
    else { const left = dueAt - nowMs, total = hours * 36e5; out.response = [`Respond in ${span(left)}`, left < total * .25 ? 'amber' : 'blue']; if (left < total * .25) { out.risk++; out.score += 50; } }
    const dueDay = Date.parse(r.due_date + 'T23:59:59');
    const days = Math.ceil((dueDay - nowMs) / 864e5);
    if (days < 0) { out.resolve = [`Overdue ${-days}d`, 'red']; out.breached++; out.score += 80 - days; }
    else if (days <= 3) { out.resolve = [days === 0 ? 'Due today' : `Due in ${days}d`, 'amber']; out.risk++; out.score += 40; }
    else out.resolve = [`Due in ${days}d`, 'gray'];
    return out;
  }
  function slaPanel() {
    const open = rows('repairs').filter(r => !['done', 'closed'].includes(r.status));
    if (!open.length || !company().role || company().role === 'inspector') return '';
    const items = open.map(r => ({r, s: slaState(r)})).sort((a, b) => b.s.score - a.s.score);
    const breached = items.filter(i => i.s.breached).length, risk = items.filter(i => !i.s.breached && i.s.risk).length;
    const tone = breached ? 'red' : risk ? 'amber' : 'green';
    const list = items.slice(0, 8).map(({r, s}) => [`<strong>${e(r.title)}</strong><small>${e(tenantFor(r.contract_id).name || '')} · ${e(propertyFor(r.contract_id).name || '')}</small>`, badge(r.urgency), badge(...s.response), badge(...s.resolve)]);
    const [eh, ed] = slaOf('Emergency'), [uh, ud] = slaOf('Urgent'), [rh, rd] = slaOf('Routine');
    return panel('Response deadlines', `${breached} breached · ${risk} at risk · ${items.length - breached - risk} on track. Emergency ${eh}h/${ed}d, Urgent ${uh}h/${ud}d, Routine ${rh}h/${rd}d (first response / resolution).`,
      table(['Repair', 'Urgency', 'First response', 'Resolve by'], list), isManager() ? tool('Edit deadlines', 'sla-edit', '', 'small') : '').replace('class="panel"', `class="panel sla-panel ${tone}"`);
  }

  /* ---------- Automation ---------- */
  const TRIGGER_TEXT = {invoice_overdue: n => `an invoice is ${n} or more days overdue`, invoice_due_soon: n => `an invoice falls due within ${n} days`, contract_ending: n => `a tenancy ends within ${n} days`, repair_open: n => `a repair has been open for ${n} days`, proof_pending: n => `a payment receipt has waited ${n} days for review`};
  const ACTION_TEXT = {create_task: r => `create a task for ${r.owner}`, notify_staff: () => 'notify the team in Haven', notify_tenant: () => 'send the tenant an in-app notice'};
  const PRESETS = [
    ['Rent nudge: call the tenant at 5 days late', {trigger: 'invoice_overdue', days: 5, action: 'create_task', title: 'Call {tenant} about {invoice} ({amount} open, {days} days late)', owner: 'Finance team'}],
    ['Friendly in-app reminder at 3 days late', {trigger: 'invoice_overdue', days: 3, action: 'notify_tenant', title: 'Rent reminder', body: 'Invoice {invoice} still shows {amount} open. Please pay, or send your proof of payment in Haven.'}],
    ['Heads-up 3 days before rent is due', {trigger: 'invoice_due_soon', days: 3, action: 'notify_tenant', title: 'Rent due soon', body: 'Invoice {invoice} for {amount} is due on {date}.'}],
    ['Plan the move-out 60 days before a lease ends', {trigger: 'contract_ending', days: 60, action: 'create_task', title: 'Plan move-out for {tenant} at {property} (ends {date})', owner: 'Property team'}],
    ['Flag repairs open for 14 days', {trigger: 'repair_open', days: 14, action: 'notify_staff', title: 'Repair still open after {days} days: {invoice}', body: '{tenant} at {property} is still waiting.'}],
    ['Chase unreviewed receipts after 2 days', {trigger: 'proof_pending', days: 2, action: 'notify_staff', title: 'Receipt waiting {days} days: {invoice}', body: '{tenant} submitted {amount}.'}],
  ];
  function automationPage() {
    const rules = rows('automation_rules');
    const list = rules.map(r => [`<strong>${e(r.name)}</strong>`, `When ${e(TRIGGER_TEXT[r.trigger](r.days))}`, `${e(ACTION_TEXT[r.action](r))}<small>${e(r.title)}</small>`, r.enabled ? badge('On', 'green') : badge('Paused', 'gray'), `<div class="actions">${tool(r.enabled ? 'Pause' : 'Resume', 'rule-toggle', r.id)}${tool('Delete', 'rule-delete', r.id)}</div>`]);
    const runs = rows('automation_runs').slice(0, 12).map(x => [e(dateLabel(x.ran_at)), e((byId('automation_rules', x.rule_id).name) || 'Deleted rule'), e(x.summary)]);
    return heading('Automation', 'Rules handle routine follow-up. They only create tasks and in-app notices, never legal letters or payments, and each rule acts once per record.', tool('Preview & run now', 'rules-run', '', '') + tool('Add from preset', 'rule-preset', '', '') + tool('New rule', 'rule-new', '', 'primary'), 'OPERATIONS')
      + panel('Rules', 'Checked automatically every few hours, or run them yourself', table(['Rule', 'Trigger', 'Action', 'Status', ''], list, 'No rules yet. Start from a preset.'))
      + panel('Recent activity', 'What the rules did lately', table(['When', 'Rule', 'Result'], runs, 'Nothing has run yet.'))
      + notice('Placeholders you can use in titles and messages: {tenant} {property} {invoice} {amount} {days} {date}. For repairs, {invoice} is the repair title.');
  }
  const ruleFields = (v = {}) => [['name', 'Rule name', 'text', {value: v.name || ''}],
    selectField('trigger', 'When', Object.keys(TRIGGER_TEXT).map(k => [k, TRIGGER_TEXT[k]('N')]), {value: v.trigger}),
    ['days', 'N (days)', 'number', {value: v.days ?? 5, step: 1, min: 0}],
    selectField('action', 'Then', [['create_task', 'Create a task'], ['notify_staff', 'Notify the team'], ['notify_tenant', 'Notify the tenant (in-app)']], {value: v.action}),
    ['title', 'Task title or notice headline', 'text', {value: v.title || '', wide: true}],
    ['body', 'Message (for notices)', 'textarea', {value: v.body || '', required: false, wide: true}],
    ['owner', 'Task owner', 'text', {value: v.owner || 'Property team', required: false}]];
  const createRule = d => api('/api/automation-rules', {method: 'POST', body: {...d, company_id: state.company, days: Number(d.days)}});

  /* ---------- Templates & custom fields ---------- */
  const ENTITIES = {tenant: ['Tenant', 'tenants'], property: ['Property', 'properties'], contract: ['Contract', 'contracts'], owner: ['Owner', 'owners']};
  const BUILTIN = ['today', 'company.name', 'company.address', 'company.kvk', 'user.name', 'tenant.name', 'tenant.email', 'tenant.phone', 'property.name', 'property.address', 'property.city', 'property.postcode', 'property.area', 'property.rooms', 'property.energy_label', 'contract.start_date', 'contract.end_date', 'contract.signed_on', 'contract.deposit', 'rent.basic', 'rent.service', 'rent.total', 'owner.name', 'arrears.count', 'arrears.total', 'arrears.oldest_due'];
  const mergeKeys = () => [...BUILTIN, ...rows('custom_fields').map(f => `${f.entity}.${f.key}`)];
  const recordLabel = (entity, r) => entity === 'contract' ? `${tenantFor(r.id).name || '?'} · ${propertyFor(r.id).name || '?'}` : r.name;
  function templatesPage() {
    const templates = rows('doc_templates');
    const list = templates.map(t => [`<strong>${e(t.name)}</strong>${t.archived ? '<small>Archived</small>' : ''}`, badge(t.kind, 'gray'), `<div class="actions">${t.archived ? '' : tool('Generate PDF', 'tpl-generate', t.id, 'small primary')}${tool('Edit', 'tpl-edit', t.id)}${tool(t.archived ? 'Restore' : 'Archive', 'tpl-archive', t.id)}</div>`]);
    const fields = rows('custom_fields').map(f => [`<strong>${e(f.label)}</strong>`, `<code>${e(f.entity)}.${e(f.key)}</code>`, e(ENTITIES[f.entity][0]), e(f.field_type), isManager() ? tool('Delete', 'field-delete', f.id) : '']);
    const chips = mergeKeys().map(k => `<button type="button" class="merge-chip" data-tool="copy-key" data-id="${e(k)}" title="Copy">{{${e(k)}}}</button>`).join('');
    const editRow = Object.entries(ENTITIES).map(([k, [label]]) => tool(`Edit ${label.toLowerCase()} data`, 'values-edit', k)).join('');
    return heading('Templates & custom fields', 'Write a letter or lease once with merge fields, then generate a finished PDF for any tenancy.', (templates.length ? '' : tool('Add starter templates', 'tpl-starters', '', '')) + tool('New template', 'tpl-new', '', 'primary'), 'OPERATIONS')
      + panel('Templates', 'Missing data is caught before a document is created', table(['Template', 'Type', ''], list, 'No templates yet. Add the starters to begin.'))
      + panel('Merge fields', 'Click one to copy it, then paste it into a template', `<div class="panel-body merge-chips">${chips}</div>`)
      + panel('Custom fields', 'Extra data you want on tenants, properties, contracts or owners', table(['Label', 'Merge field', 'Applies to', 'Type', ''], fields, 'No custom fields yet.') + `<div class="panel-body actions">${tool('New custom field', 'field-new', '', 'small primary')}${editRow}</div>`);
  }
  const tplFields = (t = {}) => [['name', 'Template name', 'text', {value: t.name || ''}], selectField('kind', 'Type', ['Letter', 'Notice', 'Lease', 'Other'], {value: t.kind || 'Letter'}),
    ['body', 'Text. Use # for a heading, ## for a section, - for bullets and {{tenant.name}} style merge fields.', 'textarea', {value: t.body || '', wide: true}]];

  async function downloadPdf(path, body) {
    const res = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': state.me.csrf}, body: JSON.stringify(body)});
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(Array.isArray(d.detail) ? d.detail.map(x => x.msg).join('; ') : d.detail || 'Could not create the document.'); }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await res.blob());
    a.download = (res.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)?.[1] || 'document.pdf';
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  function generateModal(id) {
    const t = byId('doc_templates', id);
    if (!rows('contracts').length) return toast('Add a tenancy first.');
    const needsArrears = /\{\{\s*arrears\./.test(t.body);
    const overdue = cid => rows('invoices').filter(i => i.contract_id === cid && i.due_date < (state.data.today || today())).reduce((s, i) => s + Math.max(0, balance(i)), 0);
    let opts = contractOptions().map(([v, l]) => ({v, l, due: overdue(Number(v))}));
    if (needsArrears) opts = opts.sort((a, b) => b.due - a.due).map(o => ({...o, l: o.due ? `${o.l} (overdue ${money(o.due)})` : `${o.l} (nothing overdue)`}));
    modal(`Generate: ${t.name}`, `${needsArrears ? '<p class="modal-note">This letter is about unpaid rent. Tenancies with overdue invoices are listed first.</p>' : ''}<div class="field"><label for="g-contract">Tenancy</label><select id="g-contract">${opts.map(o => `<option value="${o.v}">${e(o.l)}</option>`).join('')}</select></div>
      <div class="field checkbox wide"><input type="checkbox" id="g-save"><label for="g-save">Save a copy in the document vault</label></div>
      <div class="field checkbox wide"><input type="checkbox" id="g-share"><label for="g-share">Also share it with the tenant (needs a saved copy)</label></div>
      <div id="g-preview" class="tpl-preview" hidden></div><div class="form-error" id="g-error" role="alert"></div>
      <div class="modal-footer">${tool('Preview', 'tpl-preview', id, '')}${tool('Create PDF', 'tpl-create', id, 'small primary')}</div>`);
  }
  function valuesModal(entity) {
    const defs = rows('custom_fields').filter(f => f.entity === entity);
    const recs = rows(ENTITIES[entity][1]);
    if (!defs.length) return toast(`Create a custom field for ${ENTITIES[entity][0].toLowerCase()}s first.`);
    if (!recs.length) return toast('There are no records yet.');
    const inputs = rid => defs.map(f => `<div class="field"><label>${e(f.label)}</label><input data-field="${f.id}" type="${f.field_type === 'number' ? 'number' : f.field_type === 'date' ? 'date' : 'text'}" step="any" value="${e(rows('custom_values').find(v => v.field_id === f.id && v.entity_id === Number(rid))?.value || '')}"></div>`).join('');
    modal(`${ENTITIES[entity][0]} data`, `<div class="field"><label for="v-rec">Record</label><select id="v-rec">${recs.map(r => `<option value="${r.id}">${e(recordLabel(entity, r))}</option>`).join('')}</select></div><div id="v-inputs" class="form-grid">${inputs(recs[0].id)}</div><div class="form-error" id="v-error" role="alert"></div><div class="modal-footer">${tool('Save', 'values-save', entity, 'small primary')}</div>`);
    $('#v-rec').addEventListener('change', ev => { $('#v-inputs').innerHTML = inputs(ev.target.value); });
  }

  const actions = {
    'sla-edit': () => {
      const f = [];
      for (const u of ['Emergency', 'Urgent', 'Routine']) { const [h, d] = slaOf(u); f.push([`h_${u}`, `${u}: first response within (hours)`, 'number', {value: h, step: 1, min: 1}], [`d_${u}`, `${u}: resolved within (days)`, 'number', {value: d, step: 1, min: 1}]); }
      formModal('Response deadlines', f, d => api('/api/sla-policies', {method: 'POST', body: {company_id: state.company, policies: Object.fromEntries(['Emergency', 'Urgent', 'Routine'].map(u => [u, {response_hours: Number(d[`h_${u}`]), resolve_days: Number(d[`d_${u}`])}]))}}), {submit: 'Save deadlines', intro: 'New repairs get these deadlines when they are reported. Existing repairs keep theirs.'});
    },
    'rule-new': () => formModal('New automation rule', ruleFields(), createRule, {submit: 'Create rule', success: 'Rule created.', intro: 'Rules only create tasks and in-app notices. Use placeholders like {tenant} and {amount}.'}),
    'rule-preset': () => formModal('Add from preset', [selectField('preset', 'Preset', PRESETS.map((p, i) => [i, p[0]]))], d => { const [name, v] = PRESETS[Number(d.preset)]; return createRule({name, body: '', owner: 'Property team', ...v}); }, {submit: 'Add rule', success: 'Rule added.'}),
    'rule-toggle': id => { const r = byId('automation_rules', id); return api(`/api/automation-rules/${id}/toggle`, {method: 'POST', body: {enabled: !r.enabled}}).then(refresh); },
    'rule-delete': id => { if (confirm('Delete this rule? Past activity stays in the audit log.')) return api(`/api/automation-rules/${id}`, {method: 'DELETE'}).then(refresh).then(() => toast('Rule deleted.')); },
    'rules-run': async () => {
      const r = await api('/api/automation-rules/run', {method: 'POST', body: {company_id: state.company, dry_run: true}});
      const body = r.report.length ? r.report.map(x => `<li><strong>${e(x.name)}</strong>: ${x.matches} match${x.matches === 1 ? '' : 'es'}${x.samples.length ? `<small>${x.samples.map(e).join('<br>')}</small>` : ''}</li>`).join('') : '<li>No active rules.</li>';
      modal('Preview', `<p class="modal-note">${r.total ? `Running now would act on ${r.total} record${r.total === 1 ? '' : 's'}.` : 'Nothing to do right now.'}</p><ul class="run-preview">${body}</ul><div class="modal-footer">${tool('Close', 'modal-close', '', '')}${r.total ? tool(`Run ${r.total} action${r.total === 1 ? '' : 's'}`, 'rules-run-real', '', 'small primary') : ''}</div>`);
    },
    'rules-run-real': async () => { const r = await api('/api/automation-rules/run', {method: 'POST', body: {company_id: state.company}}); closeModal(); await refresh(); toast(`Done: ${r.total} action${r.total === 1 ? '' : 's'}.`); },
    'modal-close': () => closeModal(),
    'tpl-new': () => formModal('New template', tplFields(), d => api('/api/templates', {method: 'POST', body: {...d, company_id: state.company}}), {submit: 'Save template', success: 'Template saved.'}),
    'tpl-edit': id => { const t = byId('doc_templates', id); formModal('Edit template', tplFields(t), d => api(`/api/templates/${id}`, {method: 'PATCH', body: d}), {submit: 'Save changes'}); },
    'tpl-archive': id => api(`/api/templates/${id}/archive`, {method: 'POST', body: {archived: !byId('doc_templates', id).archived}}).then(refresh),
    'tpl-starters': () => api('/api/templates/starters', {method: 'POST', body: {company_id: state.company}}).then(refresh).then(() => toast('Starter templates added.')),
    'tpl-generate': id => generateModal(id),
    'tpl-preview': async id => {
      const err = $('#g-error'); err.textContent = '';
      try { const r = await api(`/api/templates/${id}/preview`, {method: 'POST', body: {contract_id: Number($('#g-contract').value)}}); const box = $('#g-preview'); box.hidden = false; box.textContent = r.text; if (r.missing.length) err.textContent = (r.hints.length ? r.hints.join(' ') : 'Missing data: ' + r.missing.join(', ')); }
      catch (ex) { err.textContent = ex.message; }
    },
    'tpl-create': async id => {
      const err = $('#g-error'); err.textContent = '';
      const body = {contract_id: Number($('#g-contract').value), save: $('#g-save').checked, share: $('#g-share').checked};
      try {
        if (body.save) { await api(`/api/templates/${id}/generate`, {method: 'POST', body}); closeModal(); await refresh(); toast(body.share ? 'Saved and shared with the tenant.' : 'Saved to the document vault.'); }
        else { await downloadPdf(`/api/templates/${id}/generate`, body); closeModal(); toast('PDF downloaded.'); }
      } catch (ex) { err.textContent = ex.message; }
    },
    'field-new': () => formModal('New custom field', [selectField('entity', 'Applies to', Object.entries(ENTITIES).map(([k, v]) => [k, v[0]])), ['label', 'Label (shown to staff)', 'text'], ['key', 'Key for merge fields (e.g. passport_no)', 'text', {hint: 'Lowercase letters, digits and underscores. Used as {{tenant.passport_no}}.'}], selectField('field_type', 'Type', ['text', 'number', 'date'])], d => api('/api/custom-fields', {method: 'POST', body: {...d, company_id: state.company}}), {submit: 'Create field'}),
    'field-delete': id => { if (confirm('Delete this field and all values stored in it?')) return api(`/api/custom-fields/${id}`, {method: 'DELETE'}).then(refresh); },
    'values-edit': entity => valuesModal(entity),
    'values-save': async entity => {
      const values = {}; document.querySelectorAll('#v-inputs [data-field]').forEach(i => { values[i.dataset.field] = i.value; });
      try { await api('/api/custom-values', {method: 'POST', body: {company_id: state.company, entity, entity_id: Number($('#v-rec').value), values}}); closeModal(); await refresh(); toast('Saved.'); }
      catch (ex) { $('#v-error').textContent = ex.message; }
    },
    'copy-key': id => { const text = `{{${id}}}`; (navigator.clipboard?.writeText(text) || Promise.reject()).then(() => toast(`Copied ${text}`)).catch(() => toast(text)); },
  };
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-tool]');
    if (!el || !actions[el.dataset.tool]) return;
    ev.preventDefault();
    Promise.resolve(actions[el.dataset.tool](el.dataset.id)).catch(err => toast(err.message));
  });
})();
