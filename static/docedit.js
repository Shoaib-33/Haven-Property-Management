'use strict';
/* The contract as a document you edit in place, set up like the PDF: letterhead with the logo, key-facts table, articles with the
   English translation under each clause, signature block. Every part has a pencil. Details Haven does not know yet are inputs right
   in the text. HavenDoc.mount(host, options) -> {text(), missing(), complete()}. */
(() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const GAP = /\{\{\s*([a-z_.0-9]+)\s*\}\}/g;
  const LABELS = {
    'contract.term': 'duration (Dutch)', 'contract.term_en': 'duration (English)', 'company.payment_instructions': 'how the tenant pays (IBAN, reference)',
    'tenant.phone': 'tenant phone', 'company.kvk': 'KvK number', 'company.address': 'company address', 'property.postcode': 'postcode', 'property.energy_label': 'energy label'
  };
  const label = k => LABELS[k] || k.replace(/[._]/g, ' ');

  function mount(host, o) {
    const state = {lines: String(o.text || '').split('\n'), fills: {}, editing: null};
    const pen = (i, what) => `<button type="button" class="de-pen" data-de="pen" data-i="${i}" aria-label="Edit ${esc(what)}">✎</button>`;
    const del = i => `<button type="button" class="de-del" data-de="del" data-i="${i}" aria-label="Remove this paragraph" title="Remove this paragraph">×</button>`;

    // text with open details (the {{gaps}}) as inputs, everything else escaped
    function withGaps(raw) {
      let out = '', last = 0, m;
      GAP.lastIndex = 0;
      while ((m = GAP.exec(raw))) {
        out += esc(raw.slice(last, m.index));
        const v = state.fills[m[1]] || '';
        out += `<input class="de-gap ${v ? 'filled' : ''}" data-de-gap="${esc(m[1])}" value="${esc(v)}" placeholder="${esc(label(m[1]))}" size="${Math.max(10, (v || label(m[1])).length)}" aria-label="${esc(label(m[1]))}">`;
        last = m.index + m[0].length;
      }
      return out + esc(raw.slice(last));
    }
    const editor = (i, value, rows = 3) => `<textarea class="de-edit" id="de-edit" data-i="${i}" rows="${rows}">${esc(value)}</textarea><span class="de-hint">Press Ctrl+Enter or click away to keep. Esc cancels.</span>`;

    function render() {
      const L = state.lines, out = [];
      let i = 0, article = null;
      const sig = o.signature, askRows = (o.asks || []).map(a => `<li>${esc(a.label)}${a.required ? ' *' : ''}<span class="de-blank"></span></li>`).join('');
      while (i < L.length) {
        const line = L[i], t = line.trimEnd();
        if (!t.trim()) { i++; continue; }
        if (t.startsWith('# ')) {
          const [nl, en] = t.slice(2).split(' | ');
          out.push(state.editing === i ? `<div class="de-block">${editor(i, t.slice(2), 1)}</div>` : `<div class="de-block de-title"><h1>${esc(nl)}</h1>${en ? `<p class="de-sub">${esc(en)}</p>` : ''}${pen(i, 'the title')}</div>`);
        } else if (t.startsWith('## ')) {
          const [nl, en] = t.slice(3).split(' | ');
          article = i;
          out.push(state.editing === i ? `<div class="de-block">${editor(i, t.slice(3), 1)}</div>` : `<div class="de-block de-h2"><h2>${esc(nl)}</h2>${en ? `<p class="de-h2en">${esc(en)}</p>` : ''}${pen(i, 'the heading')}</div>`);
        } else if (t.startsWith('|') && t.endsWith('|')) {
          const rows = [];
          while (i < L.length && L[i].trimEnd().startsWith('|') && L[i].trimEnd().endsWith('|')) { rows.push(i); i++; }
          out.push('<table class="de-kv"><tbody>' + rows.map(r => {
            const cells = L[r].trim().slice(1, -1).split('|').map(c => c.trim());
            const raw = cells.slice(1).join(' | ');
            return `<tr><th>${esc(cells[0])}</th><td>${state.editing === r ? editor(r, raw, 2) : `<span class="de-val">${withGaps(raw)}</span>${pen(r, cells[0])}`}</td></tr>`;
          }).join('') + '</tbody></table>');
          continue;
        } else if (t.startsWith('(EN) ')) {
          out.push(`<div class="de-block de-en">${state.editing === i ? editor(i, t.slice(5), 3) : `<p>${withGaps(t.slice(5))}</p>${pen(i, 'the English text')}${del(i)}`}</div>`);
        } else {
          out.push(`<div class="de-block de-p">${state.editing === i ? editor(i, t, 4) : `<p>${withGaps(t)}</p>${pen(i, 'this paragraph')}${del(i)}`}</div>`);
        }
        // after the last line of an article, offer to add text
        const next = L.slice(i + 1).find(x => x.trim());
        if (article !== null && (next === undefined || next.startsWith('## ')) && !t.startsWith('## ') && !t.startsWith('# ')) out.push(`<button type="button" class="de-add" data-de="add" data-i="${i}">+ Add a paragraph to this article</button>`);
        i++;
      }
      const company = o.company || {};
      host.innerHTML = `<div class="de-sheet">
        <header class="de-head"><div class="de-logo">${o.logo ? `<img src="${esc(o.logo)}" alt="${esc(company.name)} logo">` : `<strong>${esc(company.name)}</strong>`}</div>
          <div class="de-company"><strong>${esc(company.name)}</strong><br>${esc(company.address)}${company.kvk ? `<br>KvK ${esc(company.kvk)}` : ''}<br>Kenmerk / Reference: <b>assigned when sent</b></div></header>
        <div class="de-rule"></div>
        ${out.join('')}
        ${askRows ? `<div class="de-block de-appendix"><h2>Bijlage: gegevens huurder</h2><p class="de-h2en">Appendix: tenant details, filled in by the tenant before signing</p><ul>${askRows}</ul></div>` : ''}
        <div class="de-block de-signs"><h2>Ondertekening</h2><p class="de-h2en">Signatures</p><div class="de-sigrow">
          <div>${sig && sig.signature ? `<img src="${esc(sig.signature)}" alt="Landlord signature">` : '<span class="de-sigspace"></span>'}<hr><b>Verhuurder / Landlord</b><br>${sig && sig.name ? `${esc(sig.name)}<br>${esc(sig.title || '')}` : 'To be signed when you approve'}</div>
          <div><span class="de-sigspace"></span><hr><b>Huurder / Tenant</b><br>To be signed by the tenant</div></div></div>
      </div>`;
      const box = document.getElementById('de-edit');
      if (box) { box.focus(); box.selectionStart = box.value.length; autosize(box); }
      o.onChange && o.onChange();
    }
    const autosize = el => { el.style.height = 'auto'; el.style.height = Math.min(360, el.scrollHeight + 4) + 'px'; };

    // write the edited text back to its line, keeping the line's markers
    function commit() {
      const box = document.getElementById('de-edit');
      if (!box) return;
      const i = Number(box.dataset.i), L = state.lines, t = L[i].trimEnd(), v = box.value.replace(/\n+/g, ' ').trim();
      if (t.startsWith('# ')) L[i] = '# ' + v;
      else if (t.startsWith('## ')) L[i] = '## ' + v;
      else if (t.startsWith('|')) L[i] = '| ' + t.slice(1, -1).split('|')[0].trim() + ' | ' + v + ' |';
      else if (t.startsWith('(EN) ')) L[i] = '(EN) ' + v;
      else L[i] = v;
      state.editing = null;
    }
    host.addEventListener('click', ev => {
      const b = ev.target.closest('[data-de]');
      if (!b) return;
      ev.preventDefault();
      const i = Number(b.dataset.i);
      if (b.dataset.de === 'pen') { commit(); state.editing = i; render(); }
      else if (b.dataset.de === 'del') { commit(); const wasEn = /^\(EN\) /.test(state.lines[i].trimStart()); state.lines.splice(i, 1); if (!wasEn && state.lines[i] && /^\(EN\) /.test(state.lines[i].trimStart())) state.lines.splice(i, 1); render(); }
      else if (b.dataset.de === 'add') { commit(); state.lines.splice(i + 1, 0, '', 'Nieuwe tekst.', '(EN) New text.'); state.editing = i + 2; render(); }
    });
    host.addEventListener('focusout', ev => {
      if (ev.target.id === 'de-edit') setTimeout(() => { if (!document.activeElement || document.activeElement.id !== 'de-edit') { commit(); render(); } }, 0);
    });
    host.addEventListener('keydown', ev => {
      if (ev.target.id !== 'de-edit') return;
      if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); state.editing = null; render(); }
      else if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey || ev.target.rows === 1)) { ev.preventDefault(); commit(); render(); }
    }, true);
    host.addEventListener('input', ev => {
      if (ev.target.id === 'de-edit') autosize(ev.target);
      const g = ev.target.dataset && ev.target.dataset.deGap;
      if (g) {
        state.fills[g] = ev.target.value.trim();
        host.querySelectorAll(`[data-de-gap="${CSS.escape(g)}"]`).forEach(x => { if (x !== ev.target) x.value = ev.target.value; x.classList.toggle('filled', !!state.fills[g]); x.size = Math.max(10, x.value.length || label(g).length); });
        o.onChange && o.onChange();
      }
    });
    render();
    const filled = () => state.lines.join('\n').replace(GAP, (m, k) => state.fills[k] || m);
    const missing = () => [...new Set([...filled().matchAll(GAP)].map(m => m[1]))];
    return {text: () => (commit(), filled()), missing, complete: () => missing().length === 0, labelOf: label};
  }
  window.HavenDoc = {mount};
})();
