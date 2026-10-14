'use strict';
/* Staff pages for owners, portfolios and monthly owner statements.
   Plugs into app.js (navigation, page rendering, helpers) without editing it. */
(() => {
  if (typeof navGroups === 'undefined' || typeof renderPage !== 'function') return;
  navGroups[0][1].push(['owners', 'Owners & portfolios', 'building']);
  navGroups[1][1].push(['statements', 'Owner statements', 'file']);

  const baseAllowed = allowedPage;
  allowedPage = page => {
    const role = company().role;
    if (page === 'statements') return ['admin', 'manager', 'finance'].includes(role);
    if (page === 'owners') return role !== 'inspector';
    return baseAllowed(page);
  };
  const baseRender = renderPage;
  renderPage = () => state.page === 'owners' ? ownersPage() : state.page === 'statements' ? statementsPage() : baseRender();

  const own = (label, action, id = '', cls = 'small') => `<button type="button" class="button ${cls}" data-own="${action}" data-id="${id}">${e(label)}</button>`;
  const lastMonth = () => { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1); return d.toLocaleDateString('en-CA').slice(0, 7); };
  const monthLabel = p => new Intl.DateTimeFormat('en-GB', {month: 'long', year: 'numeric'}).format(new Date(p + '-01T12:00:00'));
  const ownerOf = id => byId('owners', id);
  const unitsOf = pid => rows('properties').filter(p => p.portfolio_id === pid);
  const canManage = () => ['admin', 'manager', 'property', 'finance'].includes(company().role);
  const canFinance = () => ['admin', 'manager', 'finance'].includes(company().role);
  const statusTone = {draft: 'amber', published: 'blue', paid: 'green'};

  function portfolioCard(p) {
    const units = unitsOf(p.id);
    const occupied = units.filter(u => activeContract(u.id)).length;
    const pct = units.length ? Math.round(occupied / units.length * 100) : 0;
    const roll = units.reduce((s, u) => { const c = activeContract(u.id); if (!c) return s; const r = rentFor(c.id); return s + (r.basic_cents || 0) + (r.service_cents || 0); }, 0);
    const owner = ownerOf(p.owner_id);
    return `<article class="portfolio-card">
      <header><div><h3>${e(p.name)}</h3><small>${e(owner.name || 'No owner')} · ${p.fee_percent}% management fee</small></div>${canManage() ? own('Edit', 'edit-portfolio', p.id) : ''}</header>
      <div class="pf-metrics"><div><strong>${units.length}</strong><small>units</small></div><div><strong>${pct}%</strong><small>occupied</small></div><div><strong>${money(roll)}</strong><small>monthly rent roll</small></div></div>
      <div class="pf-bar" role="img" aria-label="${pct}% occupied"><span data-width="${pct}"></span></div>
      <div class="pf-units">${units.length ? units.map(u => `<span class="unit-chip ${activeContract(u.id) ? '' : 'vacant'}">${e(u.name)}</span>`).join('') : '<small>No units yet. Edit the portfolio to add units.</small>'}</div>
    </article>`;
  }

  function ownersPage() {
    const owners = rows('owners');
    const accountOf = id => rows('owner_accounts').find(a => a.owner_id === id);
    const ownerRows = owners.map(o => {
      const acc = accountOf(o.id);
      const access = acc ? (acc.enabled ? badge('Portal active', 'green') : badge('Access paused', 'amber')) : badge('Not invited', 'gray');
      const actions = [own('Edit', 'edit-owner', o.id)];
      if (canManage()) actions.push(acc ? own(acc.enabled ? 'Pause access' : 'Restore access', acc.enabled ? 'pause-owner' : 'restore-owner', o.id) : own('Invite', 'invite-owner', o.id, 'small primary'));
      return [`<strong>${e(o.name)}</strong><small>${e(o.email)}${o.phone ? ' · ' + e(o.phone) : ''}</small>`, String(rows('portfolios').filter(p => p.owner_id === o.id).length), access, `<div class="actions">${actions.join('')}</div>`];
    });
    const portfolios = rows('portfolios');
    return heading('Owners & portfolios', 'Group units into portfolios, give each owner a portal and report on the rent that belongs to them.', canManage() ? own('New owner', 'new-owner', '', '') + own('New portfolio', 'new-portfolio', '', 'primary') : '', 'PORTFOLIO')
      + (portfolios.length ? `<div class="portfolio-grid">${portfolios.map(portfolioCard).join('')}</div>` : panel('No portfolios yet', 'A portfolio links units to one owner and a management fee.', empty('Start with an owner', 'Add an owner, then create a portfolio and choose its units.')))
      + panel('Owners', 'People and companies that own the units you manage', table(['Owner', 'Portfolios', 'Portal', ''], ownerRows, 'No owners yet.'))
      + ownerDocsPanel();
  }

  const DOC_CATEGORIES = ['Insurance', 'Deed / title', 'Valuation', 'Energy label', 'Inspection report', 'Tax & levies', 'Other'];
  function ownerDocsPanel() {
    const list = rows('owner_documents').map(s => [`<strong>${e(s.title)}</strong><small>${e(s.category)}</small>`, e(ownerOf(s.owner_id).name || ''), s.from_owner ? badge('Uploaded by owner', 'blue') : badge('Shared by you', 'gray'), s.revoked ? badge('Revoked', 'gray') : e(dateLabel(s.shared_at)),
      `<div class="actions"><a class="button small" href="/api/documents/${s.document_id}/file" target="_blank" rel="noopener">Open</a>${s.revoked || !canManage() ? '' : own('Revoke', 'revoke-doc', s.id)}</div>`]);
    return panel('Owner documents', 'Insurance papers, deeds and valuations shared with owners. Owners can upload documents too.', table(['Document', 'Owner', 'Source', 'Shared', ''], list, 'Nothing shared yet.'), canManage() ? own('Share a document', 'share-doc', '', 'small primary') : '');
  }

  function statementsPage() {
    const statements = rows('owner_statements');
    const pending = statements.filter(s => s.status === 'draft').length;
    const toPay = statements.filter(s => s.status === 'published').reduce((sum, s) => sum + Math.max(0, s.net_cents), 0);
    const stats = `<div class="summary-grid"><div class="stat"><div class="stat-top"><span>Drafts to approve</span></div><div class="stat-value">${pending}</div></div><div class="stat"><div class="stat-top"><span>Payouts to make</span></div><div class="stat-value">${money(toPay)}</div><div class="stat-foot">Published, not yet recorded as paid</div></div><div class="stat"><div class="stat-top"><span>Statements</span></div><div class="stat-value">${statements.length}</div></div></div>`;
    const list = statements.map(s => {
      const p = byId('portfolios', s.portfolio_id);
      const actions = [`<a class="button small" href="/api/owner-statements/${s.id}/pdf" target="_blank" rel="noopener">PDF</a>`];
      if (s.status === 'draft') { actions.push(own('Approve & publish', 'publish', s.id, 'small primary')); actions.push(own('Delete', 'delete-draft', s.id)); }
      if (s.status === 'published') actions.push(own('Record payout', 'paid', s.id, 'small primary'));
      return [`<strong>${e(monthLabel(s.period))}</strong><small>${e(p.name || '')}</small>`, money(s.collected_cents), '−' + money(s.fee_cents), '−' + money(s.expenses_cents), `<strong>${money(s.net_cents)}</strong>`, badge(s.status[0].toUpperCase() + s.status.slice(1), statusTone[s.status]) + (s.paid_on ? `<small>${e(dateLabel(s.paid_on))} · ${e(s.payout_reference || '')}</small>` : ''), `<div class="actions">${actions.join('')}</div>`];
    });
    const expenses = rows('owner_expenses').slice(0, 25).map(x => [e(dateLabel(x.incurred_on)), e(byId('portfolios', x.portfolio_id).name || ''), e(x.category), e(x.description), money(x.amount_cents)]);
    return heading('Owner statements', 'Rent is calculated from verified payments. A second person approves it before the owner sees it. Payouts are recorded by hand; Haven never moves money.', own('Add expense', 'new-expense', '', '') + own('Generate statement', 'generate', '', 'primary'), 'FINANCE')
      + stats
      + panel('Statements', 'Frozen once created; fix mistakes by deleting a draft and generating it again', table(['Month', 'Collected', 'Fee', 'Expenses', 'Net to owner', 'Status', ''], list, 'No statements yet. Generate one for the last completed month.'))
      + panel('Expenses paid for owners', 'Included in the statement for the month they were incurred', table(['Date', 'Portfolio', 'Category', 'Description', 'Amount'], expenses, 'No expenses recorded.'));
  }

  const ownerFields = (o = {}) => [['name', 'Owner or company name', 'text', {value: o.name || ''}], ['email', 'Email (also used for their portal login)', 'email', {value: o.email || ''}], ['phone', 'Phone', 'text', {value: o.phone || '', required: false}], ['payout_details', 'Payout notes (shown to staff only, no bank integration)', 'textarea', {value: o.payout_details || '', required: false, wide: true}]];
  const ownerOptions = () => rows('owners').map(o => [o.id, o.name]);

  function unitCheckboxes(pid) {
    return rows('properties').map(p => [`unit_${p.id}`, `${p.name}${p.portfolio_id && p.portfolio_id !== pid ? ' (moves from ' + (byId('portfolios', p.portfolio_id).name || 'another portfolio') + ')' : ''}`, 'checkbox', {value: p.portfolio_id === pid && pid ? 1 : '', required: false}]);
  }
  const chosenUnits = data => Object.keys(data).filter(k => k.startsWith('unit_') && data[k] === true).map(k => Number(k.slice(5)));

  // Statements are calculated per portfolio (an owner's group of properties), so one must exist first.
  const needPortfolio = () => {
    const hasOwner = rows('owners').length > 0;
    toast(hasOwner ? 'A statement is made for a portfolio (the properties one owner owns). Create one here first.' : 'First add the property owner, then a portfolio with their properties. Then statements can be generated.');
    state.page = 'owners'; state.filter = 'All'; render(); window.scrollTo(0, 0);
  };
  const actions = {
    'new-owner': () => formModal('New owner', ownerFields(), d => api('/api/owners', {method: 'POST', body: {...d, company_id: state.company}}), {submit: 'Add owner', success: 'Owner added.'}),
    'edit-owner': id => { const o = ownerOf(id); formModal('Edit owner', ownerFields(o), d => api(`/api/owners/${id}`, {method: 'PATCH', body: d}), {submit: 'Save changes'}); },
    'invite-owner': id => createInvitation('owner', Number(id)),
    'pause-owner': id => api('/api/accounts/access', {method: 'POST', body: {kind: 'owner', target_id: Number(id), enabled: false}}).then(refresh).then(() => toast('Portal access paused.')),
    'restore-owner': id => api('/api/accounts/access', {method: 'POST', body: {kind: 'owner', target_id: Number(id), enabled: true}}).then(refresh).then(() => toast('Portal access restored.')),
    'share-doc': () => {
      if (!rows('owners').length) return toast('Add an owner first.');
      const docs = rows('documents').filter(d => !['payment proof', 'supplier invoice', 'deposit evidence', 'contractor agreement'].includes(String(d.kind).toLowerCase())).map(d => [d.id, `#${d.id} · ${d.name}`]);
      if (!docs.length) return toast('Upload the document on the Documents page first.');
      formModal('Share a document with an owner', [selectField('owner_id', 'Owner', ownerOptions()), selectField('document_id', 'Document', docs), selectField('category', 'Category', DOC_CATEGORIES), ['title', 'Title shown to the owner (optional)', 'text', {required: false}]],
        d => api('/api/owner-documents', {method: 'POST', body: {company_id: state.company, owner_id: Number(d.owner_id), document_id: Number(d.document_id), category: d.category, title: d.title}}), {submit: 'Share with owner', success: 'Shared. The owner has been notified.', intro: 'Financial evidence such as payment receipts and supplier invoices cannot be shared with owners.'});
    },
    'revoke-doc': id => { if (confirm('Remove the owner\'s access to this document?')) return api(`/api/owner-documents/${id}/revoke`, {method: 'POST', body: {}}).then(refresh).then(() => toast('Access removed.')); },
    'new-portfolio': () => {
      if (!rows('owners').length) return toast('Add an owner first.');
      formModal('New portfolio', [['name', 'Portfolio name', 'text'], selectField('owner_id', 'Owner', ownerOptions()), ['fee_percent', 'Management fee (% of collected rent)', 'number', {value: '6', step: '0.1'}], ...unitCheckboxes(0)],
        d => api('/api/portfolios', {method: 'POST', body: {company_id: state.company, name: d.name, owner_id: Number(d.owner_id), fee_percent: d.fee_percent, property_ids: chosenUnits(d)}}), {submit: 'Create portfolio', intro: 'Choose which units belong to this owner. A unit can be in one portfolio at a time.'});
    },
    'edit-portfolio': id => {
      const p = byId('portfolios', id);
      formModal('Edit portfolio', [['name', 'Portfolio name', 'text', {value: p.name}], selectField('owner_id', 'Owner', ownerOptions(), {value: p.owner_id}), ['fee_percent', 'Management fee (%)', 'number', {value: p.fee_percent, step: '0.1'}], ...unitCheckboxes(p.id)],
        d => api(`/api/portfolios/${id}`, {method: 'PATCH', body: {name: d.name, owner_id: Number(d.owner_id), fee_percent: d.fee_percent, property_ids: chosenUnits(d)}}), {submit: 'Save portfolio'});
    },
    'generate': () => {
      if (!rows('portfolios').length) return needPortfolio();
      formModal('Generate owner statement', [selectField('portfolio_id', 'Portfolio', rows('portfolios').map(p => [p.id, p.name])), ['period', 'Month', 'month', {value: lastMonth(), max: lastMonth()}]],
        d => api('/api/owner-statements/generate', {method: 'POST', body: {company_id: state.company, portfolio_id: Number(d.portfolio_id), period: d.period}}),
        {submit: 'Calculate draft', intro: 'Haven adds up payments verified in that month, subtracts the management fee and recorded expenses, and saves a draft for a second person to approve.'});
    },
    'publish': id => formModal('Approve and publish', [['confirmed', 'I checked the collected rent, expenses and management fee.', 'checkbox']], () => api(`/api/owner-statements/${id}/publish`, {method: 'POST', body: {confirmed: true}}), {submit: 'Publish to owner', intro: 'The owner is notified and can download the PDF. Figures are frozen from this moment. Someone other than the person who generated it must approve.', success: 'Statement published.'}),
    'paid': id => {
      const s = byId('owner_statements', id);
      formModal('Record payout', [dayField('paid_on', 'Date paid'), ['reference', 'Your payment reference', 'text'], ['confirmed', `I paid ${money(Math.max(0, s.net_cents))} to the owner outside Haven.`, 'checkbox', {required: s.net_cents > 0}]], d => api(`/api/owner-statements/${id}/paid`, {method: 'POST', body: {paid_on: d.paid_on, reference: d.reference, confirmed: !!d.confirmed}}), {submit: 'Mark as paid', success: 'Payout recorded.'});
    },
    'delete-draft': id => { if (confirm('Delete this draft? You can generate it again afterwards.')) api(`/api/owner-statements/${id}`, {method: 'DELETE'}).then(refresh).then(() => toast('Draft deleted.')); },
    'new-expense': () => {
      if (!rows('portfolios').length) return needPortfolio();
      formModal('Record an owner expense', [selectField('portfolio_id', 'Portfolio', rows('portfolios').map(p => [p.id, p.name])), selectField('property_id', 'Unit (optional)', rows('properties').filter(p => p.portfolio_id).map(p => [p.id, p.name]), {optional: true, required: false}), dayField('incurred_on', 'Date', today()), selectField('category', 'Category', ['Repairs & maintenance', 'Insurance', 'Taxes & levies', 'Owners association (VvE)', 'Cleaning & utilities', 'Other']), ['description', 'Description', 'text'], amountField('amount', 'Amount (€)'), selectField('document_id', 'Invoice or receipt (optional)', docOptions(), {optional: true, required: false})],
        d => api('/api/owner-expenses', {method: 'POST', body: {...d, company_id: state.company, portfolio_id: Number(d.portfolio_id)}}), {submit: 'Save expense', intro: 'It will appear on the owner statement for the month of the date you choose.'});
    },
  };
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-own]');
    if (!el || !actions[el.dataset.own]) return;
    ev.preventDefault();
    Promise.resolve(actions[el.dataset.own](el.dataset.id)).catch(err => toast(err.message));
  });
})();
