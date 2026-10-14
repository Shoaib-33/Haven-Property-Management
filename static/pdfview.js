'use strict';
/* View a PDF inside the portal instead of downloading it: invoices (staff and tenant) and contracts.
   Any link to one of those PDFs opens a viewer with "Open in new tab" and "Download". */
(() => {
  const VIEWABLE = [/^\/api\/documents\/\d+\/file$/, /^\/api\/tenant\/documents\/\d+\/file$/, /^\/api\/invoices\/\d+\/pdf$/, /^\/api\/tenant\/invoices\/\d+\/pdf$/, /^\/api\/lease-offers\/\d+\/file$/, /^\/api\/tenant\/lease-offers\/\d+\/file$/];
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  let dialog = null;

  function build() {
    dialog = el('dialog', 'pdf-viewer');
    dialog.setAttribute('aria-label', 'Document preview');
    const head = el('div', 'pv-head'), title = el('strong', 'pv-title', 'Document');
    const tools = el('div', 'pv-tools');
    const tab = el('a', 'button small', 'Open in new tab'); tab.target = '_blank'; tab.rel = 'noopener'; tab.dataset.pv = 'tab';
    const down = el('a', 'button small primary', 'Download'); down.dataset.pv = 'download';
    const close = el('button', 'icon-button', '×'); close.type = 'button'; close.setAttribute('aria-label', 'Close preview');
    close.addEventListener('click', () => dialog.close());
    tools.append(tab, down, close); head.append(title, tools);
    const frame = el('iframe', 'pv-frame'); frame.title = 'PDF preview';
    dialog.append(head, frame);
    dialog.addEventListener('click', ev => { if (ev.target === dialog) dialog.close(); });
    dialog.addEventListener('close', () => { frame.removeAttribute('src'); });
    document.body.append(dialog);
  }

  function open(path, label) {
    if (!dialog) build();
    dialog.querySelector('.pv-title').textContent = label || 'Document';
    dialog.querySelector('[data-pv="tab"]').href = path;
    dialog.querySelector('[data-pv="download"]').href = path + (path.includes('?') ? '&' : '?') + 'download=1';
    dialog.querySelector('.pv-frame').src = path + '#zoom=page-fit&navpanes=0';
    if (!dialog.open) dialog.showModal();
  }

  document.addEventListener('click', ev => {
    const a = ev.target.closest && ev.target.closest('a[href]');
    if (!a) return;
    let u;
    try { u = new URL(a.getAttribute('href'), location.href); } catch { return; }
    if (u.origin !== location.origin || u.searchParams.has('download') || !VIEWABLE.some(r => r.test(u.pathname))) return;
    ev.preventDefault();
    ev.stopPropagation();
    open(u.pathname, a.dataset.pvTitle || (/invoice/.test(u.pathname) ? 'Invoice' : /lease-offers/.test(u.pathname) ? 'Contract' : 'Document'));
  }, true);

  window.HavenPdf = {open};
})();
