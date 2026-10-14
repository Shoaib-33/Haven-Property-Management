'use strict';
/* Interactive overview analytics: rent performance, occupancy, arrears aging, forecast and ticket health.
   Pure SVG (no libraries) so it works under the strict CSP. Injected into the existing overview page. */
(() => {
  if (typeof renderPage !== 'function') return;
  const prevRender = renderPage;
  const view = {months: 6, hide: new Set()};
  try { view.months = Number(localStorage.getItem('haven_dash_months')) === 12 ? 12 : 6; } catch { /* storage unavailable */ }

  renderPage = () => {
    const html = prevRender();
    if (state.page !== 'overview' || !state.company) return html;
    return withPeriodBar(html).replace('<div class="overview-grid">', () => analytics() + '<div class="overview-grid">');
  };

  function withPeriodBar(html) {
    const start = html.indexOf('<div class="page-heading">');
    if (start < 0 || !canSeeFinance()) return html;
    let depth = 0, i = start;
    const re = /<div\b|<\/div>/g; re.lastIndex = start;
    for (let m; (m = re.exec(html));) { depth += m[0] === '</div>' ? -1 : 1; if (depth === 0) { i = m.index + m[0].length; break; } }
    return html.slice(0, i) + periodBar() + html.slice(i);
  }
  const todayStr = () => state.data.today || today();
  const monthKey = (y, m) => `${y}-${String(m).padStart(2, '0')}`;
  const shift = (key, delta) => { const [y, m] = key.split('-').map(Number); const d = new Date(y, m - 1 + delta, 1); return monthKey(d.getFullYear(), d.getMonth() + 1); };
  const monthName = (key, long) => new Intl.DateTimeFormat('en-GB', {month: long ? 'long' : 'short', ...(long ? {year: 'numeric'} : {})}).format(new Date(key + '-01T12:00:00'));
  const compact = cents => { const v = (cents || 0) / 100; return v >= 1e6 ? `€${(v / 1e6).toFixed(1)}M` : v >= 1000 ? `€${(v / 1000).toFixed(v >= 1e4 ? 0 : 1)}k` : `€${Math.round(v)}`; };
  const niceMax = max => { if (max <= 0) return 1000; const p = Math.pow(10, Math.floor(Math.log10(max))); const n = max / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; };
  const canSeeFinance = () => ['admin', 'manager', 'finance'].includes(company().role);

  /* ---------- data ---------- */
  const monthlyStat = key => (state.data.monthly_stats || []).find(m => m.period === key);
  function rentSeries(n) {
    const end = todayStr().slice(0, 7);
    const keys = Array.from({length: n}, (_, i) => shift(end, i - n + 1));
    return keys.map(k => {
      const m = monthlyStat(k) || {billed: 0, collected: 0};
      return {key: k, billed: m.billed || 0, collected: m.collected || 0, rate: m.billed > 0 ? Math.round(m.collected / m.billed * 100) : null};
    });
  }
  function forecastSeries() {
    const start = todayStr().slice(0, 7);
    return Array.from({length: 7}, (_, i) => {
      const key = shift(start, i), first = key + '-01', lastDay = new Date(Number(key.slice(0, 4)), Number(key.slice(5)), 0).getDate(), last = `${key}-${String(lastDay).padStart(2, '0')}`;
      let total = 0, units = 0, ending = 0;
      for (const c of rows('contracts')) {
        if (c.status === 'ended' && c.end_date && c.end_date < first) continue;
        if (c.start_date > last || (c.end_date && c.end_date < first)) continue;
        const v = rows('rent_versions').filter(r => r.contract_id === c.id && r.effective_from <= first).sort((a, b) => b.effective_from.localeCompare(a.effective_from))[0] || rows('rent_versions').filter(r => r.contract_id === c.id).sort((a, b) => a.effective_from.localeCompare(b.effective_from))[0];
        if (!v) continue;
        total += v.basic_cents + v.service_cents; units++;
        if (c.end_date && c.end_date <= last) ending++;
      }
      return {key, total, units, ending};
    });
  }
  function agingBuckets() {
    const t = todayStr();
    const buckets = [{label: '1–7 days', from: 1, to: 7, cents: 0, n: 0}, {label: '8–30 days', from: 8, to: 30, cents: 0, n: 0}, {label: '31–60 days', from: 31, to: 60, cents: 0, n: 0}, {label: '60+ days', from: 61, to: 1e9, cents: 0, n: 0}];
    for (const inv of rows('invoices')) {
      const open = balance(inv);
      if (open <= 0 || inv.due_date >= t) continue;
      const age = Math.round((new Date(t + 'T12:00:00') - new Date(inv.due_date + 'T12:00:00')) / 864e5);
      const b = buckets.find(x => age >= x.from && age <= x.to);
      if (b) { b.cents += open; b.n++; }
    }
    return buckets;
  }

  /* ---------- charts ---------- */
  function rentChart() {
    const data = rentSeries(chartN()), W = 640, H = 270, L = 46, R = 12, T = 22, B = 34;
    const max = niceMax(Math.max(...data.map(d => Math.max(view.hide.has('billed') ? 0 : d.billed, view.hide.has('collected') ? 0 : d.collected))));
    const cw = (W - L - R) / data.length, bw = Math.min(26, cw * .32), y = v => T + (H - T - B) * (1 - v / max);
    const selected = state.overviewPeriod || todayStr().slice(0, 7);
    let g = '';
    for (let i = 0; i <= 4; i++) { const v = max * i / 4; g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="grid"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end" class="axis">${e(compact(v))}</text>`; }
    data.forEach((d, i) => {
      const cx = L + cw * i + cw / 2, sel = d.key === selected;
      const hb = view.hide.has('billed') ? 0 : (H - T - B) * d.billed / max, hc = view.hide.has('collected') ? 0 : (H - T - B) * d.collected / max;
      g += `<g class="bar-group ${sel ? 'sel' : ''}" data-dash-tip="${e(`${monthName(d.key, true)}|Billed ${money(d.billed)}|Collected ${money(d.collected)}|${d.rate === null ? 'Nothing billed' : d.rate + '% collected'}`)}" data-dash-period="${d.key}" tabindex="0" role="button" aria-label="${e(monthName(d.key, true))}: billed ${e(money(d.billed))}, collected ${e(money(d.collected))}">
        <rect x="${cx - cw / 2}" y="${T}" width="${cw}" height="${H - T - B}" class="hit"/>
        ${hb ? `<rect x="${cx - bw - 2}" y="${y(d.billed)}" width="${bw}" height="${hb}" rx="4" class="bar billed"/>` : ''}
        ${hc ? `<rect x="${cx + 2}" y="${y(d.collected)}" width="${bw}" height="${hc}" rx="4" class="bar collected"/>` : ''}
        <text x="${cx}" y="${H - 14}" text-anchor="middle" class="axis ${sel ? 'on' : ''}">${e(monthName(d.key))}</text>
        ${d.rate !== null && !view.hide.has('collected') ? `<text x="${cx + bw / 2 + 2}" y="${Math.max(T + 8, y(Math.max(d.collected, 0)) - 6)}" text-anchor="middle" class="pct">${d.rate}%</text>` : ''}</g>`;
    });
    const empty = data.every(d => d.billed === 0) ? `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" class="axis">No invoices in this period yet</text>` : '';
    return `<svg viewBox="0 0 ${W} ${H}" class="dash-svg" role="img" aria-label="Rent billed versus collected per month">${g}${empty}</svg>`;
  }

  function donut() {
    const props = rows('properties'), occ = props.filter(p => activeContract(p.id)).length, res = props.filter(p => !activeContract(p.id) && upcomingContract(p.id)).length, vac = props.length - occ - res;
    const r = 52, c = 2 * Math.PI * r, pct = props.length ? Math.round(occ / props.length * 100) : 0;
    const seg = (val, color, offset, page, label) => val ? `<circle cx="70" cy="70" r="${r}" fill="none" stroke="${color}" stroke-width="16" stroke-dasharray="${c * val / props.length} ${c}" stroke-dashoffset="${-offset}" transform="rotate(-90 70 70)" class="seg" data-dash-go="${page}" data-dash-tip="${e(label)}" tabindex="0" role="button" aria-label="${e(label)}"/>` : '';
    return `<div class="donut-wrap"><svg viewBox="0 0 140 140" class="donut" role="img" aria-label="${pct}% occupied"><circle cx="70" cy="70" r="${r}" fill="none" stroke="#e8f0eb" stroke-width="16"/>
      ${seg(occ, '#176b55', 0, 'properties', `${occ} occupied units`)}${seg(res, '#6c9bd1', c * occ / (props.length || 1), 'properties', `${res} reserved units`)}${seg(vac, '#e0b85a', c * (occ + res) / (props.length || 1), 'properties', `${vac} vacant units`)}
      <text x="70" y="68" text-anchor="middle" class="donut-n">${pct}%</text><text x="70" y="86" text-anchor="middle" class="axis">occupied</text></svg>
      <ul class="legend-list"><li><i class="dot" data-c="g"></i>${occ} occupied</li>${res ? `<li><i class="dot" data-c="blue"></i>${res} reserved</li>` : ''}<li><i class="dot" data-c="a"></i>${vac} vacant</li><li class="muted">${props.length} units in total</li></ul></div>`;
  }

  function aging() {
    const b = agingBuckets(), max = Math.max(...b.map(x => x.cents), 1), total = b.reduce((s, x) => s + x.cents, 0);
    if (!total) return `<div class="dash-ok"><span>${icon('check')}</span><p><strong>No overdue rent</strong><br><small>Every issued invoice is paid or not yet due.</small></p></div>`;
    return `<ul class="aging">${b.map((x, i) => `<li data-dash-go="arrears" data-dash-tip="${e(`${x.label}|${x.n} invoice${x.n === 1 ? '' : 's'}|${money(x.cents)}`)}" tabindex="0" role="button"><span class="al">${e(x.label)}</span><span class="ab"><span class="af tone${i}" data-width="${Math.max(x.cents ? 4 : 0, Math.round(x.cents / max * 100))}"></span></span><span class="av">${e(compact(x.cents))}</span></li>`).join('')}</ul><p class="dash-foot">Total overdue <strong>${money(total)}</strong></p>`;
  }

  function forecast() {
    const data = forecastSeries(), W = 640, H = 230, L = 44, R = 14, T = 16, B = 28;
    const vals = data.map(d => d.total), max = niceMax(Math.max(...vals, 1)), min = 0;
    const x = i => L + (W - L - R) * i / (data.length - 1), y = v => T + (H - T - B) * (1 - (v - min) / (max - min));
    const pts = data.map((d, i) => [x(i), y(d.total)]);
    const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
    const area = `${line} L${x(data.length - 1)} ${H - B} L${x(0)} ${H - B} Z`;
    let g = '';
    for (let i = 0; i <= 3; i++) { const v = max * i / 3; g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="grid"/><text x="${L - 7}" y="${y(v) + 4}" text-anchor="end" class="axis">${e(compact(v))}</text>`; }
    const dots = data.map((d, i) => `<g class="pt" data-dash-tip="${e(`${monthName(d.key, true)}|Expected rent roll ${money(d.total)}|${d.units} tenanc${d.units === 1 ? 'y' : 'ies'}${d.ending ? ' · ' + d.ending + ' ending' : ''}`)}" tabindex="0"><circle cx="${pts[i][0]}" cy="${pts[i][1]}" r="14" class="hit"/><circle cx="${pts[i][0]}" cy="${pts[i][1]}" r="4.5" class="dotc ${d.ending ? 'warn' : ''}"/><text x="${pts[i][0]}" y="${H - 9}" text-anchor="middle" class="axis">${e(monthName(d.key))}</text></g>`).join('');
    return `<svg viewBox="0 0 ${W} ${H}" class="dash-svg" role="img" aria-label="Expected monthly rent for the next six months">${g}<path d="${area}" class="area"/><path d="${line}" class="line"/>${dots}</svg>`;
  }

  function ticketHealth() {
    const open = rows('repairs').filter(r => !['done', 'closed'].includes(r.status));
    const slaDue = r => (Date.parse(r.response_due_at || '') || Date.parse(r.created_at) + 5 * 24 * 36e5);
    const unanswered = open.filter(r => !r.responded_at);
    const late = open.filter(r => (!r.responded_at && Date.now() > slaDue(r)) || Date.parse(r.due_date + 'T23:59:59') < Date.now());
    const by = u => open.filter(r => r.urgency === u).length;
    const unreadReq = rows('requests').filter(r => r.status === 'open').length;
    const total = Math.max(open.length, 1);
    const bars = [['Emergency', by('Emergency'), 'red'], ['Urgent', by('Urgent'), 'amber'], ['Routine', by('Routine'), 'blue']];
    return `<div class="th-stats"><div data-dash-go="portal-inbox"><strong>${open.length}</strong><small>open repairs</small></div><div data-dash-go="portal-inbox" class="${late.length ? 'bad' : ''}"><strong>${late.length}</strong><small>past deadline</small></div><div data-dash-go="portal-inbox"><strong>${unanswered.length + unreadReq}</strong><small>awaiting a reply</small></div></div>
      <div class="th-bar" role="img" aria-label="Open repairs by urgency">${bars.map(([l, n, c]) => n ? `<span class="th-seg ${c}" data-width="${Math.round(n / total * 100)}" data-dash-tip="${e(`${l}|${n} open`)}"></span>` : '').join('')}</div>
      <ul class="legend-list inline">${bars.map(([l, n, c]) => `<li><i class="dot" data-c="${c}"></i>${l} ${n}</li>`).join('')}</ul>`;
  }

  /* ---------- month-by-month analysis ---------- */
  const monthOf = v => String(v || '').slice(0, 7);
  const chartN = () => view.months === 'year' ? 12 : view.months;
  function statsFor(keys) {
    const repairs = rows('repairs'), requests = rows('requests'), threads = rows('portal_messages').filter(m => !m.thread_id);
    return keys.map(k => {
      const m = monthlyStat(k) || {invoices: 0, billed: 0, collected: 0, paid: 0};
      return {key: k, billed: m.billed || 0, collected: m.collected || 0, rate: m.billed > 0 ? Math.round(m.collected / m.billed * 100) : null,
        outstanding: Math.max(0, (m.billed || 0) - (m.collected || 0)), invoices: m.invoices || 0, paid: m.paid || 0,
        repairs: repairs.filter(r => monthOf(r.reported_on || r.created_at) === k).length,
        tickets: requests.filter(r => monthOf(r.created_at) === k).length + threads.filter(m2 => monthOf(m2.created_at) === k).length};
    });
  }
  const withPrev = keys => { const all = statsFor([shift(keys[0], -1), ...keys]); return all.slice(1).map((d, i) => ({...d, prev: all[i]})); };
  const delta = (cur, prev, goodUp = true) => {
    if (prev === null || prev === undefined || (!prev && !cur)) return '<span class="mt-delta flat">–</span>';
    if (!prev) return '<span class="mt-delta up">new</span>';
    const pct = Math.round((cur - prev) / Math.abs(prev) * 100);
    if (!pct) return '<span class="mt-delta flat">0%</span>';
    const good = (pct > 0) === goodUp;
    return `<span class="mt-delta ${good ? 'good' : 'bad'}">${pct > 0 ? '▲' : '▼'} ${Math.abs(pct)}%</span>`;
  };
  function yearOptions() {
    const thisYear = Number(todayStr().slice(0, 4));
    const years = [thisYear, thisYear - 1, ...(state.data.monthly_stats || []).map(m => Number(String(m.period).slice(0, 4))), ...rows('repairs').map(r => Number(monthOf(r.reported_on || r.created_at).slice(0, 4)))].filter(y => y >= 2000 && y <= thisYear + 1);
    const lo = Math.min(...years), out = [];
    for (let y = thisYear + 1; y >= lo; y--) out.push(y);
    return out;
  }
  function periodBar() {
    const selected = state.overviewPeriod || todayStr().slice(0, 7), now = todayStr().slice(0, 7), live = selected === now;
    return `<div class="period-bar" role="group" aria-label="Choose the month to analyse"><span class="pb-label">${icon('calendar')} Viewing</span><button type="button" class="icon-button pb-step" data-dash-shift="-1" aria-label="Previous month">&lsaquo;</button>${pickerFields(selected)}<button type="button" class="icon-button pb-step" data-dash-shift="1" aria-label="Next month" ${selected >= shift(now, 1) ? 'disabled' : ''}>&rsaquo;</button><button type="button" class="button small ${live ? '' : 'primary'}" data-dash-period="${now}" ${live ? 'disabled' : ''}>This month</button><span class="pb-note">${live ? 'Current month, still in progress' : 'Past period: figures are final once all payments are in'}</span></div>`;
  }
  function pickerFields(selected) {
    const [sy, sm] = selected.split('-').map(Number);
    const months = Array.from({length: 12}, (_, i) => `<option value="${i + 1}" ${i + 1 === sm ? 'selected' : ''}>${e(monthName(monthKey(2000, i + 1), true).replace(/ \d+$/, ''))}</option>`).join('');
    const years = yearOptions().map(y => `<option value="${y}" ${y === sy ? 'selected' : ''}>${y}</option>`).join('');
    return `<label class="pb-field"><span>Month</span><select data-dash-pick="month">${months}</select></label><label class="pb-field"><span>Year</span><select data-dash-pick="year">${years}</select></label>`;
  }
  function monthlyAnalysis() {
    const selected = state.overviewPeriod || todayStr().slice(0, 7), [sy] = selected.split('-').map(Number);
    const keys = view.months === 'year' ? Array.from({length: 12}, (_, i) => monthKey(sy, i + 1)) : Array.from({length: view.months}, (_, i) => shift(todayStr().slice(0, 7), i - view.months + 1));
    const data = withPrev(keys), cur = withPrev([selected])[0];
    const kpi = (label, value, change, foot) => `<div class="mt-kpi"><small>${e(label)}</small><strong>${e(value)}</strong>${change}<span>${e(foot)}</span></div>`;
    const kpis = `<div class="mt-kpis">${kpi('Billed', money(cur.billed), delta(cur.billed, cur.prev.billed), `${cur.invoices} invoice${cur.invoices === 1 ? '' : 's'}`)}
      ${kpi('Collected', money(cur.collected), delta(cur.collected, cur.prev.collected), cur.rate === null ? 'Nothing billed' : cur.rate + '% of billed')}
      ${kpi('Still open', money(cur.outstanding), delta(cur.outstanding, cur.prev.outstanding, false), `${cur.invoices - cur.paid} unpaid invoice${cur.invoices - cur.paid === 1 ? '' : 's'}`)}
      ${kpi('New repairs', String(cur.repairs), delta(cur.repairs, cur.prev.repairs, false), 'reported this month')}
      ${kpi('New tickets', String(cur.tickets), delta(cur.tickets, cur.prev.tickets, false), 'requests and messages')}</div>`;
    const maxBill = Math.max(...data.map(d => d.billed), 1);
    const body = data.slice().reverse().map(d => {
      const blank = d.invoices === 0 && d.repairs === 0 && d.tickets === 0;
      return `<tr class="${d.key === cur.key ? 'sel' : ''} ${blank ? 'blank' : ''}" data-dash-period="${d.key}" tabindex="0" role="button" aria-label="${e(monthName(d.key, true))}">
        <td><strong>${e(monthName(d.key, true))}</strong></td>
        <td class="num">${d.invoices ? e(money(d.billed)) : '—'}<span class="mt-spark"><i data-width="${Math.round(d.billed / maxBill * 100)}"></i></span></td>
        <td class="num">${d.invoices ? e(money(d.collected)) : '—'} ${d.invoices ? delta(d.collected, d.prev.collected) : ''}</td>
        <td class="num ${d.outstanding ? 'warn' : ''}">${d.invoices ? e(money(d.outstanding)) : '—'}</td>
        <td>${d.rate === null ? '—' : `<span class="mt-rate"><span class="mt-rate-bar"><i class="${d.rate >= 90 ? 'ok' : d.rate >= 60 ? 'mid' : 'low'}" data-width="${Math.min(100, d.rate)}"></i></span>${d.rate}%</span>`}</td>
        <td class="num">${d.invoices ? `${d.paid}/${d.invoices}` : '—'}</td><td class="num">${d.repairs}</td><td class="num">${d.tickets}</td></tr>`;
    }).join('');
    const tot = data.reduce((a, d) => ({billed: a.billed + d.billed, collected: a.collected + d.collected, repairs: a.repairs + d.repairs, tickets: a.tickets + d.tickets}), {billed: 0, collected: 0, repairs: 0, tickets: 0});
    const rate = tot.billed ? Math.round(tot.collected / tot.billed * 100) : null;
    const inRows = data.some(d => d.key === cur.key);
    const note = cur.key === todayStr().slice(0, 7) ? ' This month is still in progress, so its change versus last month is not final.' : '';
    return `<div class="mt-head"><span>Showing <strong>${e(monthName(cur.key, true))}</strong>.${inRows ? ' Pick any row to change the focus.' : ' It is outside the table below. Switch to Year to see that year.'}${note}</span></div>${kpis}
      <div class="table-wrap"><table class="mt-table"><thead><tr><th>Month</th><th>Billed</th><th>Collected</th><th>Still open</th><th>Collection rate</th><th>Invoices paid</th><th>Repairs</th><th>Tickets</th></tr></thead><tbody>${body}</tbody>
      <tfoot><tr><td><strong>${view.months === 'year' ? sy : 'Last ' + view.months + ' months'}</strong></td><td class="num">${e(money(tot.billed))}</td><td class="num">${e(money(tot.collected))}</td><td class="num">${e(money(Math.max(0, tot.billed - tot.collected)))}</td><td>${rate === null ? '—' : rate + '%'}</td><td></td><td class="num">${tot.repairs}</td><td class="num">${tot.tickets}</td></tr></tfoot></table></div>`;
  }
  function analytics() {
    const fin = canSeeFinance();
    const legend = `<div class="legend" role="group" aria-label="Series"><button type="button" class="lg ${view.hide.has('billed') ? 'off' : ''}" data-dash-series="billed"><i class="dot" data-c="soft"></i>Billed</button><button type="button" class="lg ${view.hide.has('collected') ? 'off' : ''}" data-dash-series="collected"><i class="dot" data-c="g"></i>Collected</button></div>`;
    const range = `<div class="seg-toggle" role="group" aria-label="Range">${[6, 12].map(n => `<button type="button" class="${view.months === n ? 'on' : ''}" data-dash-range="${n}">${n}M</button>`).join('')}</div>`;
    const rangeYear = `<div class="seg-toggle" role="group" aria-label="Range">${[6, 12, 'year'].map(n => `<button type="button" class="${view.months === n ? 'on' : ''}" data-dash-range="${n}">${n === 'year' ? 'Year' : n + 'M'}</button>`).join('')}</div>`;
    const card = (cls, title, sub, body, extra = '') => `<section class="panel dash-card ${cls}"><div class="panel-header"><div><h2>${e(title)}</h2><p>${e(sub)}</p></div>${extra}</div><div class="dash-body">${body}</div></section>`;
    return `<div class="dash-grid" id="dash">
      ${fin ? card('wide', 'Rent performance', 'Billed versus collected. Click a month to focus the overview on it.', `${legend}<div class="chart-box">${rentChart()}</div>`, range) : ''}
      ${card('', 'Occupancy', 'Click a segment to open properties', donut())}
      ${fin ? card('full mt-card', 'Month-by-month analysis', 'Pick any month and year, or view a whole year. Each figure shows the change versus the month before.', monthlyAnalysis(), rangeYear) : ''}
      ${fin ? card('wide2', 'Expected rent, next 6 months', 'Based on active tenancies and scheduled rent changes', `<div class="chart-box">${forecast()}</div>`) : ''}
      ${fin ? card('', 'Overdue rent by age', 'Click to open arrears', aging()) : ''}
      ${card('full', 'Ticket health', 'Repairs and requests that need you. Click to open Tickets.', ticketHealth())}
    </div><div id="dash-tip" class="dash-tip" role="tooltip" hidden></div>`;
  }

  /* ---------- interaction ---------- */
  const tip = () => document.getElementById('dash-tip');
  function showTip(el, ev) {
    const t = tip(); if (!t || !el.dataset.dashTip) return;
    const [head, ...lines] = el.dataset.dashTip.split('|');
    t.innerHTML = `<strong>${e(head)}</strong>${lines.map(l => `<span>${e(l)}</span>`).join('')}`;
    t.hidden = false;
    const r = el.getBoundingClientRect();
    const x = ev && ev.clientX ? ev.clientX : r.left + r.width / 2, y = ev && ev.clientY ? ev.clientY : r.top;
    t.style.left = Math.min(window.innerWidth - t.offsetWidth - 12, Math.max(12, x + 14)) + 'px';
    t.style.top = Math.max(12, y - t.offsetHeight - 14) + 'px';
  }
  const hideTip = () => { const t = tip(); if (t) t.hidden = true; };
  document.addEventListener('mousemove', ev => { const el = ev.target.closest && ev.target.closest('[data-dash-tip]'); if (el && el.closest('#dash')) showTip(el, ev); else hideTip(); });
  document.addEventListener('focusin', ev => { const el = ev.target.closest && ev.target.closest('#dash [data-dash-tip]'); if (el) showTip(el); });
  document.addEventListener('focusout', hideTip);
  const act = el => {
    if (el.dataset.dashPeriod) { state.overviewPeriod = el.dataset.dashPeriod; render(); toast(`Showing ${monthName(el.dataset.dashPeriod, true)}`); return true; }
    if (el.dataset.dashGo) { state.page = el.dataset.dashGo; state.filter = 'All'; history.replaceState(null, '', '#' + state.page); render(); window.scrollTo(0, 0); return true; }
    if (el.dataset.dashSeries) { const k = el.dataset.dashSeries; view.hide.has(k) ? view.hide.delete(k) : view.hide.add(k); if (view.hide.size === 2) view.hide.clear(); render(); return true; }
    if (el.dataset.dashShift) { state.overviewPeriod = shift(state.overviewPeriod || todayStr().slice(0, 7), Number(el.dataset.dashShift)); render(); return true; }
    if (el.dataset.dashRange) { const v = el.dataset.dashRange; view.months = v === 'year' ? 'year' : Number(v); if (v !== 'year') { try { localStorage.setItem('haven_dash_months', view.months); } catch { /* storage unavailable */ } } render(); return true; }
    return false;
  };
  document.addEventListener('click', ev => { const el = ev.target.closest && ev.target.closest('#dash [data-dash-period],#dash [data-dash-go],#dash [data-dash-series],#dash [data-dash-range],.period-bar [data-dash-period],.period-bar [data-dash-shift]'); if (el && !el.disabled && act(el)) ev.stopPropagation(); }, true);
  document.addEventListener('change', ev => {
    const sel = ev.target.closest && ev.target.closest('.period-bar [data-dash-pick]'); if (!sel) return;
    const box = sel.closest('.period-bar'), m = box.querySelector('[data-dash-pick="month"]').value, y = box.querySelector('[data-dash-pick="year"]').value;
    state.overviewPeriod = monthKey(Number(y), Number(m)); render();
  });
  document.addEventListener('keydown', ev => { if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.closest) { const el = ev.target.closest('#dash [data-dash-period],#dash [data-dash-go]'); if (el) { ev.preventDefault(); act(el); } } });

  /* Headline figures are animated by polish.js. */
})();
