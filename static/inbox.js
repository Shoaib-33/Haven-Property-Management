'use strict';
/* Staff helpdesk: two pages on one conversation layout (list + chat + tenant context + saved replies).
   Tickets  = what tenants raise (repairs, requests, complaints), each with a status, a thread and Mark resolved.
   Inbox    = personal conversations with tenants (not tickets).
   Replaces the old table-based Tenant inbox page without editing app.js. */
(() => {
  if (typeof renderPage !== 'function' || typeof navGroups === 'undefined') return;
  const prevRender = renderPage;
  const PAGES = ['portal-inbox', 'tenant-messages'];
  const mode = () => state.page === 'tenant-messages' ? 'inbox' : 'tickets';
  const onPage = () => PAGES.includes(state.page);
  renderPage = () => onPage() ? inboxView() : prevRender();
  // Everything a tenant raises (requests, complaints, repairs) is a ticket for the landlord; personal messages stay in the Inbox.
  const opsGroup = navGroups.find(g => g[1].some(n => n[0] === 'portal-inbox'));
  if (opsGroup) {
    const entry = opsGroup[1].find(n => n[0] === 'portal-inbox');
    opsGroup[1].splice(opsGroup[1].indexOf(entry), 1);
    opsGroup[1].unshift(['tenant-messages', 'Inbox', 'message']);
    opsGroup[1].unshift(['portal-inbox', 'Tickets', 'clipboard']);
  }
  // One place for repairs: Maintenance leaves the menu for roles that see Tickets and becomes the "Board" view of Tickets.
  const repairNav = opsGroup && opsGroup[1].find(n => n[0] === 'repairs');
  const repairIdx = repairNav ? opsGroup[1].indexOf(repairNav) : -1;
  const viewSwitch = active => `<div class="view-switch" role="tablist" aria-label="Ticket view"><button type="button" class="vs-btn ${active === 'list' ? 'active' : ''}" data-action="navigate" data-page="portal-inbox">${icon('clipboard')} Tickets</button><button type="button" class="vs-btn ${active === 'board' ? 'active' : ''}" data-action="navigate" data-page="repairs">${icon('wrench')} Repair board</button></div>`;
  if (repairNav) {
    const prevRepairs = typeof repairsPage === 'function' ? repairsPage : null;
    const prevShell = render;
    render = () => {
      const unified = ['admin', 'manager'].includes(company().role || '');
      const has = opsGroup[1].includes(repairNav);
      if (unified && has) opsGroup[1].splice(opsGroup[1].indexOf(repairNav), 1);
      if (!unified && !has) opsGroup[1].splice(Math.min(repairIdx, opsGroup[1].length), 0, repairNav);
      prevShell();
      if (unified && state.page === 'repairs') {
        document.querySelector('.nav-item[data-page="portal-inbox"]')?.classList.add('active');
        const main = document.getElementById('main'), h = main?.querySelector('.page-heading');
        if (h && !main.querySelector(".view-switch")) h.insertAdjacentHTML('beforebegin', viewSwitch('board'));
        const crumb = document.querySelector('.breadcrumb span'); if (crumb) crumb.textContent = 'Tickets';
      }
    };
  }
  const prevAllowed = allowedPage;
  allowedPage = page => PAGES.includes(page) ? ['admin', 'manager', 'property'].includes(company().role) : prevAllowed(page);

  const ui = {selected: null, filter: 'open', type: 'all', q: '', limit: 40, mode: 'tickets'};
  const isManager = () => ['admin', 'manager'].includes(company().role);
  const REPAIR_STATUS = ['received', 'triaged', 'planned', 'in progress', 'waiting', 'reopened'];
  const TICKET_TYPES = ['Repair', 'Request', 'Complaint', 'Payment plan', 'Move-out'];
  const typeOf = it => it.type === 'repair' ? 'Repair' : it.type === 'thread' ? 'Message' : ({'Complaint': 'Complaint', 'Payment plan': 'Payment plan', 'Termination notice': 'Move-out'}[it.kind] || 'Request');
  const number = it => ({thread: 'MSG', request: 'REQ', repair: 'REP'}[it.type]) + '-' + it.id;
  const DEFAULT_SLA = {Emergency: [4, 2], Urgent: [48, 14], Routine: [120, 42]};
  const slaHours = u => { const p = rows('sla_policies').find(x => x.urgency === u); return p ? p.response_hours : (DEFAULT_SLA[u] || DEFAULT_SLA.Routine)[0]; };
  function repairSla(r) {
    const dueAt = Date.parse(r.response_due_at || '') || (Date.parse(r.created_at) + slaHours(r.urgency) * 36e5);
    const late = !r.responded_at && Date.now() > dueAt;
    const overdue = !['done', 'closed'].includes(r.status) && Date.parse(r.due_date + 'T23:59:59') < Date.now();
    return {late, overdue};
  }
  const draftKey = k => `haven_draft_${state.company}_${k}`;
  const readDraft = k => { try { return sessionStorage.getItem(draftKey(k)) || ''; } catch { return ''; } };
  const saveDraft = (k, v) => { try { v ? sessionStorage.setItem(draftKey(k), v) : sessionStorage.removeItem(draftKey(k)); } catch { /* storage unavailable */ } };
  const isTyping = () => document.activeElement?.matches?.('input,textarea,select');

  const ago = iso => {
    const ms = Date.now() - Date.parse(iso || '');
    if (!isFinite(ms)) return '';
    const m = Math.round(ms / 6e4);
    if (m < 1) return 'now';
    if (m < 60) return `${m}m`;
    const h = Math.round(m / 60);
    if (h < 24) return `${h}h`;
    const d = Math.round(h / 24);
    return d < 14 ? `${d}d` : dateLabel(iso);
  };
  const clock = iso => new Intl.DateTimeFormat('en-GB', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}).format(new Date(iso));
  const staffName = id => (rows('staff').find(s => s.id === id) || {}).name || '';
  const tenantUser = tenantId => (rows('portal_accounts').find(a => a.tenant_id === tenantId) || {}).user_id;
  const firstName = n => String(n || '').split(' ')[0];

  function buildItems() {
    const msgs = rows('portal_messages').slice().sort((a, b) => a.id - b.id);
    const reqs = rows('requests');
    const inbox = mode() === 'inbox';
    const answered = new Map(reqs.filter(r => r.response).map(r => [`${r.contract_id}|Response: ${r.subject}`, r]));
    const hidden = new Map();  // the reply a manager gave to a request lives inside that request, so a tenant answer to it must too
    const threads = new Map();
    for (const m of msgs) {
      if (!m.thread_id && answered.has(`${m.contract_id}|${m.subject}`)) { hidden.set(m.id, answered.get(`${m.contract_id}|${m.subject}`)); continue; }
      const id = m.thread_id || m.id;
      if (!threads.has(id)) threads.set(id, []);
      threads.get(id).push(m);
    }
    const items = [];
    const followUps = new Map();
    for (const [id, list] of threads) {
      if (hidden.has(id)) { const r = hidden.get(id); followUps.set(r.id, (followUps.get(r.id) || []).concat(list)); continue; }
      if (!inbox) continue;
      const root = list[0], tenant = tenantFor(root.contract_id), uid = tenantUser(tenant.id), last = list[list.length - 1];
      const st = rows('thread_states').find(t => t.thread_id === id);
      items.push({key: `t:${id}`, type: 'thread', id, contract_id: root.contract_id, subject: String(root.subject).replace(/^Response:\s*/i, ''), tenant, last: last.created_at,
        messages: list.map(m => ({mine: uid === undefined ? true : m.sender_id !== uid, name: m.sender_name, body: m.body, at: m.created_at, doc: m.document_id})),
        needsReply: uid !== undefined && last.sender_id === uid && !(st && st.status === 'resolved'), resolved: !!(st && st.status === 'resolved'), assignee: st ? st.assignee_id : null});
    }
    for (const r of inbox ? [] : reqs) {
      const tenant = tenantFor(r.contract_id);
      const msgsR = [{mine: false, name: tenant.name, body: `${r.kind}${r.requested_date ? ` (requested date ${dateLabel(r.requested_date)})` : ''}\n${r.body}`, at: r.created_at}];
      const uid = tenantUser(tenant.id);
      // every answer the team gave is kept as its own message; the single r.response field only holds the latest one
      const answers = [...hidden].filter(([, rq]) => rq.id === r.id).map(([id]) => msgs.find(m => m.id === id)).filter(Boolean);
      const talk = answers.concat(followUps.get(r.id) || []).sort((x, y) => x.id - y.id);
      if (!talk.length && r.response) msgsR.push({mine: true, name: 'Property team', body: r.response, at: r.responded_at || r.created_at});
      for (const m of talk) msgsR.push({mine: uid === undefined ? true : m.sender_id !== uid, name: m.sender_name, body: m.body, at: m.created_at, doc: m.document_id});
      const lastTalk = talk[talk.length - 1];
      const tenantLast = !!lastTalk && uid !== undefined && lastTalk.sender_id === uid && Date.parse(lastTalk.created_at) > Date.parse(r.responded_at || r.created_at);
      items.push({key: `r:${r.id}`, type: 'request', id: r.id, contract_id: r.contract_id, subject: r.subject, kind: r.kind, tenant, last: lastTalk && Date.parse(lastTalk.created_at) > Date.parse(r.responded_at || 0) ? lastTalk.created_at : r.responded_at || r.created_at, messages: msgsR, needsReply: r.status === 'open' || tenantLast, resolved: ['resolved', 'declined'].includes(r.status) && !tenantLast, status: r.status});
    }
    for (const r of inbox ? [] : rows('repairs')) {
      const tenant = tenantFor(r.contract_id), uid = tenantUser(tenant.id);
      const updates = rows('repair_updates').filter(u => u.repair_id === r.id && !String(u.message).startsWith('Tenant reported: ')).slice().sort((a, b) => a.id - b.id);
      const log = [{mine: false, name: tenant.name, body: r.description, at: r.created_at, doc: null}];
      for (const u of updates) {
        const fromTenant = uid !== undefined && u.user_id === uid;
        const isStatus = String(u.message).startsWith('Status: ');
        const staff = staffName(u.user_id);
        log.push({mine: !fromTenant, name: fromTenant ? tenant.name : (staff || (String(u.message).startsWith('Contractor update') ? 'Contractor' : 'Property team')), body: u.message, at: u.created_at, doc: u.document_id, system: isStatus, internal: !u.tenant_visible});
      }
      const lastTalk = log.filter(m => !m.system).pop();
      const done = ['done', 'closed'].includes(r.status);
      const sla = repairSla(r);
      items.push({key: `p:${r.id}`, type: 'repair', id: r.id, contract_id: r.contract_id, subject: r.title, kind: r.category, tenant, last: log[log.length - 1].at, messages: log, urgency: r.urgency, status: r.status, repair: r, sla,
        needsReply: !done && !lastTalk.mine, resolved: done});
    }
    const rank = it => (it.needsReply ? 4 : 0) + (it.sla && (it.sla.late || it.sla.overdue) ? 2 : 0) + (it.urgency === 'Emergency' ? 1 : 0);
    return items.sort((a, b) => (rank(b) - rank(a)) || Date.parse(b.last) - Date.parse(a.last));
  }

  const matches = (it, f) => (ui.type === 'all' || typeOf(it) === ui.type) && (f === 'all' || (f === 'open' && !it.resolved) || (f === 'reply' && it.needsReply) || (f === 'late' && it.sla && (it.sla.late || it.sla.overdue) && !it.resolved) || (f === 'resolved' && it.resolved));
  const initialsOf = n => initials(n || '?');

  function listHtml(items) {
    const filtered = items.filter(it => matches(it, ui.filter));
    // Draw 40 at a time so a long history stays fast; a search shows every match.
    const shown = ui.q.trim() ? filtered : filtered.slice(0, ui.limit);
    const more = shown.length < filtered.length ? `<li class="ib-more"><button type="button" class="button small" data-in="more">Show ${Math.min(40, filtered.length - shown.length)} more <span>(${filtered.length - shown.length} older)</span></button></li>` : '';
    return filtered.length ? shown.map(it => {
      const lastMsg = it.messages[it.messages.length - 1];
      return `<li><button type="button" class="ib-item ${ui.selected === it.key ? 'active' : ''} ${it.needsReply ? 'unread' : ''}" data-in="select" data-key="${it.key}" data-search="${e((it.tenant.name || '') + ' ' + it.subject + ' ' + lastMsg.body).toLowerCase()}">
        <span class="avatar">${e(initialsOf(it.tenant.name))}</span>
        <span class="ib-main"><span class="ib-top"><strong>${e(it.tenant.name || 'Unknown')}</strong><time>${e(ago(it.last))}</time></span>
          <span class="ib-subject"><em class="ib-num">${e(number(it))}</em> ${e(it.subject)}</span><span class="ib-preview">${e((lastMsg.mine ? 'You: ' : '') + lastMsg.body.replace(/\s+/g, ' ').slice(0, 90))}</span>
          <span class="ib-tags"><span class="ib-tag type-${e(typeOf(it).replace(/\W/g, '').toLowerCase())}">${e(typeOf(it))}</span>${it.urgency && it.urgency !== 'Routine' && !it.resolved ? `<span class="ib-tag ${it.urgency === 'Emergency' ? 'bad' : 'warn'}">${e(it.urgency)}</span>` : ''}${it.sla && (it.sla.late || it.sla.overdue) && !it.resolved ? '<span class="ib-tag bad">SLA breached</span>' : ''}${it.needsReply ? '<span class="ib-tag warn">Needs reply</span>' : ''}${it.resolved ? '<span class="ib-tag ok">Resolved</span>' : ''}${it.assignee ? `<span class="ib-tag">${e(initialsOf(staffName(it.assignee)))}</span>` : ''}</span></span></button></li>`;
    }).join('') + more : `<li class="ib-none">${ui.filter === 'reply' ? 'Nothing waiting. Everyone has an answer.' : mode() === 'inbox' ? 'No conversations here.' : 'No tickets here.'}</li>`;
  }

  function contextHtml(it) {
    const c = contract(it.contract_id), p = propertyFor(it.contract_id), t = it.tenant, r = rentFor(it.contract_id);
    const due = rows('invoices').filter(i => i.contract_id === it.contract_id).reduce((s, i) => s + Math.max(0, balance(i)), 0);
    const repairs = rows('repairs').filter(x => x.contract_id === it.contract_id && !['done', 'closed'].includes(x.status));
    return `<aside class="ib-context"><h4>Tenant</h4><p><strong>${e(t.name)}</strong><br><small>${e(t.email || '')}${t.phone ? '<br>' + e(t.phone) : ''}</small></p>
      <h4>Home</h4><p>${e(p.name || '')}<br><small>${e(p.address || '')}, ${e(p.city || '')}</small></p>
      <h4>Rent</h4><p>${r.basic_cents != null ? money((r.basic_cents || 0) + (r.service_cents || 0)) + ' / month' : '—'}<br>${due ? `<span class="ib-tag warn">${money(due)} open</span>` : '<span class="ib-tag ok">Paid up</span>'}</p>
      <h4>Open repairs</h4>${repairs.length ? `<ul>${repairs.slice(0, 3).map(x => `<li>${e(x.title)} <small>${e(x.status)}</small></li>`).join('')}</ul>` : '<p><small>None</small></p>'}
      ${it.type === 'repair' ? `<h4>Ticket</h4><p><small>Reported ${e(dateLabel(it.repair.reported_on))}<br>Resolve by ${e(dateLabel(it.repair.due_date))}<br>${it.repair.assigned_to ? 'Assigned: ' + e(it.repair.assigned_to) : 'Not assigned'}</small></p>` : ''}
      <h4>Tenancy</h4><p><small>${e(dateLabel(c.start_date))} → ${c.end_date ? e(dateLabel(c.end_date)) : 'open-ended'}</small></p></aside>`;
  }

  function headActions(it, staff) {
    if (it.type === 'thread') {
      return `<select data-in-assign aria-label="Assign to"><option value="">Unassigned</option>${staff.map(s => `<option value="${s.id}" ${s.id === it.assignee ? 'selected' : ''}>${e(s.name)}</option>`).join('')}</select>${button(it.resolved ? 'Reopen' : 'Mark resolved', 'noop', 'data-in="toggle-resolved"', it.resolved ? '' : 'primary')}`;
    }
    if (it.type === 'repair') {
      const r = it.repair, closed = it.resolved;
      const sel = closed ? '' : `<select data-in-repair-status aria-label="Repair status">${REPAIR_STATUS.map(v => `<option ${v === r.status ? 'selected' : ''}>${v}</option>`).join('')}</select>`;
      return `${it.sla.late ? '<span class="ib-tag bad">First response late</span>' : ''}${it.sla.overdue ? '<span class="ib-tag bad">Resolve-by passed</span>' : ''}${sel}${closed ? badge(r.status) + button('Reopen', 'noop', 'data-in="repair-reopen"', '') : button('Assign contractor', 'noop', 'data-in="repair-assign"', '') + button('Mark resolved…', 'noop', 'data-in="repair-complete"', 'primary')}`;
    }
    // request / complaint: talk in the thread, then close it with Mark resolved
    return `${badge(it.status)}${isManager() ? (it.resolved ? button('Reopen', 'noop', 'data-in="request-state" data-status="open"', '') : (it.status === 'in progress' ? '' : button('Start work', 'noop', 'data-in="request-state" data-status="in progress"', '')) + button('Decline', 'noop', 'data-in="request-state" data-status="declined"', '') + button('Mark resolved', 'noop', 'data-in="request-state" data-status="resolved"', 'primary')) : ''}`;
  }

  function detailHtml(it) {
    if (!it) return `<div class="ib-empty"><div class="ib-empty-art">${icon('message')}</div><h3>${mode() === 'inbox' ? 'Select a conversation' : 'Select a ticket'}</h3><p>${mode() === 'inbox' ? 'Pick a conversation on the left' : 'Pick a ticket on the left'}, or press <kbd>j</kbd> / <kbd>k</kbd> to move through them.</p></div>`;
    const staff = rows('staff').filter(s => ['admin', 'manager', 'property', 'finance'].includes(s.role));
    const canned = rows('canned_replies');
    const bubbles = it.messages.map(m => m.system ? `<div class="ib-system">${e(m.body.replace(/^Status: /, 'Status changed to '))} · ${e(clock(m.at))}</div>` : `<div class="ib-row ${m.mine ? 'mine' : 'theirs'}"><div class="ib-bubble ${m.internal ? 'internal' : ''}"><div class="ib-meta"><strong>${e(m.mine ? (m.name || 'You') : m.name)}${m.internal ? ' · internal note' : ''}</strong><time>${e(clock(m.at))}</time></div><p>${e(m.body)}</p>${m.doc ? `<a href="/api/documents/${m.doc}/file" target="_blank" rel="noopener">${icon('file')} Attachment</a>` : ''}</div></div>`).join('');
    const status = it.type === 'request'
      ? `<select id="ib-status" aria-label="Request status">${[['in progress', 'Keep in progress'], ['resolved', 'Send and resolve'], ['declined', 'Send and decline']].map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select>` : '';
    return `<div class="ib-head"><button type="button" class="icon-button ib-back" data-in="back" aria-label="Back to list">${icon('arrow')}</button><div class="ib-title"><h2><em class="ib-num">${e(number(it))}</em> ${e(it.subject)}</h2><small>${e(typeOf(it))} · ${e(it.tenant.name)} · ${e(propertyFor(it.contract_id).name || '')}${it.type === 'repair' ? ' · ' + e(it.urgency) : ''}</small></div>
      <div class="ib-head-actions">${headActions(it, staff)}</div></div>
      <div class="ib-body"><div class="ib-chat"><div class="ib-thread" id="ib-thread">${bubbles}</div>
        <form id="ib-form" class="ib-composer" data-key="${it.key}">
          <div class="ib-quick" aria-label="Quick replies">${canned.length ? canned.map(c => `<button type="button" class="ib-chip" data-in="quick" data-id="${c.id}" title="${e(c.body)}">${e(c.title)}</button>`).join('') + '<button type="button" class="ib-chip ghost" data-in="manage-replies">Manage</button>' : '<button type="button" class="ib-chip ghost" data-in="starter-replies">+ Add starter quick replies</button>'}</div>
          <textarea id="ib-body" rows="3" maxlength="5000" placeholder="Write a reply… (Ctrl+Enter to send)" required>${e(readDraft(it.key))}</textarea>
          <div class="ib-actions">${it.type === 'repair' ? '<label class="ib-internal"><input type="checkbox" id="ib-visible" checked> Visible to tenant</label>' : ''}<label class="ib-attach ${it.type === 'request' ? 'hidden' : ''}">${icon('upload')} Attach<input type="file" id="ib-file" accept="application/pdf,image/png,image/jpeg" hidden></label><span id="ib-filename" class="ib-filename"></span>
            <span class="ib-spacer"></span><button type="button" class="button small" data-in="save-reply">Save as quick reply</button>${status}<button class="button primary" type="submit" ${it.type === 'request' && !isManager() ? 'disabled title="A manager must respond to requests"' : ''}>${it.type === 'repair' ? 'Post update' : 'Send reply'}</button></div>
          <p class="form-error" id="ib-error" role="alert"></p></form></div>${contextHtml(it)}</div>`;
  }

  function inboxView() {
    if (ui.mode !== mode()) { Object.assign(ui, {mode: mode(), selected: null, filter: 'open', type: 'all', q: '', limit: 40}); }
    const inbox = mode() === 'inbox';
    const items = buildItems();
    if (ui.selected && !items.some(i => i.key === ui.selected)) ui.selected = null;
    const scoped = items.filter(i => ui.type === 'all' || typeOf(i) === ui.type);
    const counts = {open: scoped.filter(i => !i.resolved).length, reply: scoped.filter(i => i.needsReply).length, late: scoped.filter(i => i.sla && (i.sla.late || i.sla.overdue) && !i.resolved).length, resolved: scoped.filter(i => i.resolved).length};
    const chips = [['open', 'Open', counts.open], ['reply', 'Needs reply', counts.reply], ...(inbox ? [] : [['late', 'Overdue', counts.late]]), ['resolved', 'Resolved', counts.resolved], ['all', 'All', scoped.length]];
    const types = inbox ? [] : TICKET_TYPES.map(t => [t, items.filter(i => typeOf(i) === t && !i.resolved).length]).filter(([, n]) => n);
    const sel = items.find(i => i.key === ui.selected);
    const top = inbox
      ? heading('Inbox', 'Personal conversations with your tenants. Anything a tenant reports, requests or complains about arrives under Tickets instead.', button('New message', 'staff-message-new', '', 'primary'), 'TENANTS')
      : viewSwitch('list') + heading('Tickets', 'Every repair, request and complaint a tenant raises. Talk to the tenant inside the ticket and press Mark resolved when it is done.', button('Log a repair', 'form', 'data-kind="repairs"', 'primary'), 'TENANTS');
    return top
      + `<div class="inbox ${sel ? 'has-sel' : ''}" id="inbox-root"><aside class="inbox-list"><div class="ib-tools"><label class="ib-search">${icon('search')}<input type="search" id="ib-search" placeholder="${inbox ? 'Search tenants and messages' : 'Search tickets and tenants'}" value="${e(ui.q)}" autocomplete="off"></label>
        ${types.length ? `<div class="ib-types"><button type="button" class="ib-type ${ui.type === 'all' ? 'active' : ''}" data-in="type" data-key="all">All types</button>${types.map(([t, n]) => `<button type="button" class="ib-type ${ui.type === t ? 'active' : ''}" data-in="type" data-key="${e(t)}">${e(t)} <span>${n}</span></button>`).join('')}</div>` : ''}<div class="ib-filters">${chips.map(([k, l, n]) => `<button type="button" class="ib-filter ${ui.filter === k ? 'active' : ''}" data-in="filter" data-key="${k}">${l}<span>${n}</span></button>`).join('')}</div></div>
        <ul class="ib-items" id="ib-items">${listHtml(items)}</ul></aside><section class="inbox-detail" id="ib-detail">${detailHtml(sel)}</section></div>`;
  }

  function redraw() {
    const root = document.getElementById('inbox-root');
    if (!root) return render();
    const items = buildItems();
    const sel = items.find(i => i.key === ui.selected);
    root.classList.toggle('has-sel', !!sel);
    document.getElementById('ib-items').innerHTML = listHtml(items);
    document.getElementById('ib-detail').innerHTML = detailHtml(sel);
    document.querySelectorAll('.ib-filter').forEach(b => b.classList.toggle('active', b.dataset.key === ui.filter));
    applySearch();
    const th = document.getElementById('ib-thread'); if (th) th.scrollTop = th.scrollHeight;
  }
  function applySearch() {
    const q = ui.q.toLowerCase().split(/\s+/).filter(Boolean);
    document.querySelectorAll('#ib-items .ib-item').forEach(b => { b.parentElement.hidden = !q.every(w => b.dataset.search.includes(w)); });
  }
  const current = () => buildItems().find(i => i.key === ui.selected);

  async function send(form) {
    const it = current(); if (!it) return;
    const body = $('#ib-body').value.trim(), err = $('#ib-error'), btn = form.querySelector('button[type=submit]');
    err.textContent = ''; btn.disabled = true;
    try {
      if (it.type === 'request') {
        await api(`/api/requests/${it.id}/respond`, {method: 'POST', body: {response: body, status: $('#ib-status').value}});
      } else if (it.type === 'repair') {
        let document_id;
        const file = $('#ib-file').files[0];
        if (file) {
          const fd = new FormData(); fd.append('company_id', state.company); fd.append('kind', 'Repair evidence'); fd.append('contract_id', it.contract_id); fd.append('file', file);
          document_id = (await api('/api/documents', {method: 'POST', body: fd})).id;
        }
        await api(`/api/repairs/${it.id}/update`, {method: 'POST', body: {message: body, tenant_visible: $('#ib-visible').checked, ...(document_id ? {document_id} : {})}});
      } else {
        let document_id;
        const file = $('#ib-file').files[0];
        if (file) {
          const fd = new FormData(); fd.append('company_id', state.company); fd.append('kind', 'Message attachment'); fd.append('contract_id', it.contract_id); fd.append('file', file);
          document_id = (await api('/api/documents', {method: 'POST', body: fd})).id;
        }
        await api('/api/portal/messages', {method: 'POST', body: {contract_id: it.contract_id, thread_id: it.id, body, ...(document_id ? {document_id} : {})}});
      }
      saveDraft(it.key, '');
      state.data = await api(`/api/data?company_id=${state.company}`);
      redraw(); toast('Reply sent.');
      refreshBadgeSoon();
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
  }
  const refreshBadgeSoon = () => setTimeout(() => { if (typeof pollMessageBadge === 'function') pollMessageBadge(); }, 500);

  async function setState(it, patch) {
    await api(`/api/threads/${it.id}/state`, {method: 'POST', body: {status: it.resolved ? 'resolved' : 'open', assignee_id: it.assignee || null, ...patch}});
    state.data = await api(`/api/data?company_id=${state.company}`);
    redraw();
  }

  const handlers = {
    select: el => { ui.selected = el.dataset.key; redraw(); const it = current(); if (it) { const unread = rows('notifications').filter(n => n.target === state.page && !n.is_read); unread.forEach(n => api(`/api/notifications/${n.id}/read`, {method: 'POST', body: {}}).catch(() => {})); } },
    back: () => { ui.selected = null; redraw(); },
    more: () => { ui.limit += 40; redraw(); },
    filter: el => { ui.filter = el.dataset.key; ui.limit = 40; render(); },
    type: el => { ui.type = el.dataset.key; ui.limit = 40; render(); },
    'repair-assign': () => assignWork(current().id),
    'repair-complete': () => showRepairStatus(current().id),
    'repair-reopen': () => api(`/api/repairs/${current().id}/status`, {method: 'POST', body: {status: 'reopened'}}).then(() => api(`/api/data?company_id=${state.company}`)).then(d => { state.data = d; redraw(); toast('Ticket reopened.'); }),
    'toggle-resolved': () => { const it = current(); return setState(it, {status: it.resolved ? 'open' : 'resolved'}).then(() => toast(it.resolved ? 'Conversation reopened.' : 'Marked resolved. The tenant was notified.')); },
    'request-state': el => {
      const it = current(), status = el.dataset.status;
      return api(`/api/requests/${it.id}/status`, {method: 'POST', body: {status}}).then(() => api(`/api/data?company_id=${state.company}`)).then(d => { state.data = d; redraw(); toast(status === 'resolved' ? 'Ticket resolved. The tenant was notified.' : status === 'open' ? 'Ticket reopened.' : status === 'declined' ? 'Ticket declined. The tenant was notified.' : 'Ticket marked in progress.'); });
    },
    quick: el => {
      const reply = rows('canned_replies').find(c => c.id === Number(el.dataset.id)), it = current(); if (!reply || !it) return;
      const text = reply.body.replace(/\{tenant\}/g, firstName(it.tenant.name)).replace(/\{property\}/g, propertyFor(it.contract_id).name || 'your home');
      const ta = $('#ib-body'); ta.value = ta.value.trim() ? ta.value.trimEnd() + '\n\n' + text : text; ta.dispatchEvent(new Event('input')); ta.focus();
    },
    'starter-replies': () => api('/api/canned-replies/starters', {method: 'POST', body: {company_id: state.company}}).then(() => api(`/api/data?company_id=${state.company}`)).then(d => { state.data = d; redraw(); toast('Quick replies added. {tenant} and {property} are filled in automatically.'); }),
    'save-reply': () => {
      const text = $('#ib-body').value.trim();
      if (!text) return toast('Write the reply first, then save it.');
      formModal('Save as quick reply', [['title', 'Title', 'text'], ['body', 'Text', 'textarea', {value: text, wide: true}]], d => api('/api/canned-replies', {method: 'POST', body: {...d, company_id: state.company}}), {submit: 'Save quick reply', intro: 'Use {tenant} for the first name and {property} for the home.'});
    },
    'manage-replies': () => {
      modal('Quick replies', `<ul class="ib-manage">${rows('canned_replies').map(c => `<li><div><strong>${e(c.title)}</strong><small>${e(c.body)}</small></div><button class="button small" data-in="delete-reply" data-id="${c.id}">Delete</button></li>`).join('')}</ul>`);
    },
    'delete-reply': el => api(`/api/canned-replies/${el.dataset.id}`, {method: 'DELETE'}).then(() => api(`/api/data?company_id=${state.company}`)).then(d => { state.data = d; closeModal(); redraw(); toast('Quick reply deleted.'); }),
  };
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-in]');
    if (!el || !onPage() || !handlers[el.dataset.in]) return;
    ev.preventDefault(); ev.stopPropagation();
    Promise.resolve(handlers[el.dataset.in](el)).catch(err => toast(err.message));
  }, true);
  document.addEventListener('input', ev => {
    if (!onPage()) return;
    if (ev.target.id === 'ib-search') { const had = ui.q.trim(); ui.q = ev.target.value; if (!!had !== !!ui.q.trim()) redraw(); else applySearch(); }
    if (ev.target.id === 'ib-body') saveDraft(ui.selected, ev.target.value);
  });
  document.addEventListener('change', ev => {
    if (!onPage()) return;
    if (ev.target.id === 'ib-file') $('#ib-filename').textContent = ev.target.files[0] ? ev.target.files[0].name : '';
    if (ev.target.matches('[data-in-repair-status]')) { const it = current(); api(`/api/repairs/${it.id}/status`, {method: 'POST', body: {status: ev.target.value}}).then(() => api(`/api/data?company_id=${state.company}`)).then(d => { state.data = d; redraw(); toast('Status updated. The tenant was notified.'); }).catch(err => { toast(err.message); redraw(); }); }
    if (ev.target.matches('[data-in-assign]')) { const it = current(); setState(it, {assignee_id: ev.target.value ? Number(ev.target.value) : null}).then(() => toast(ev.target.value ? 'Assigned.' : 'Unassigned.')).catch(err => toast(err.message)); }
  });
  document.addEventListener('submit', ev => { if (ev.target.id === 'ib-form') { ev.preventDefault(); send(ev.target); } });
  document.addEventListener('keydown', ev => {
    if (!onPage()) return;
    if (ev.target.id === 'ib-body' && (ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') { ev.preventDefault(); $('#ib-form').requestSubmit(); return; }
    if (isTyping() || ev.ctrlKey || ev.metaKey || ev.altKey || document.querySelector('dialog[open]')) return;
    if (ev.key === 'j' || ev.key === 'k') {
      const keys = [...document.querySelectorAll('#ib-items .ib-item')].filter(b => !b.parentElement.hidden).map(b => b.dataset.key);
      if (!keys.length) return;
      const i = keys.indexOf(ui.selected);
      ui.selected = keys[Math.max(0, Math.min(keys.length - 1, i + (ev.key === 'j' ? 1 : -1)))] || keys[0];
      redraw(); document.querySelector('.ib-item.active')?.scrollIntoView({block: 'nearest'});
    }
  });
  // Scroll the open conversation to the newest message after each full render.
  new MutationObserver(() => { const th = document.getElementById('ib-thread'); if (th && !th.dataset.scrolled) { th.dataset.scrolled = '1'; th.scrollTop = th.scrollHeight; } }).observe(document.getElementById('app'), {childList: true, subtree: true});
})();
