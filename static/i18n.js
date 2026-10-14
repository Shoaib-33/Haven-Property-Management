'use strict';
/* English / Dutch for every screen.
   A language switch sits at the top of each screen. Dutch is applied on top of the existing English screens:
   the dictionary in i18n-nl.js maps exact texts and sentence patterns ({0}, {1} stand for names and numbers) to Dutch,
   and a DOM observer translates whatever the screens draw, including toasts and errors from the server.
   Dates and amounts switch to Dutch formats. Anything without a Dutch entry simply stays in English. */
(() => {
  const KEY = 'haven.lang';
  const read = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
  const save = v => { try { localStorage.setItem(KEY, v); } catch { /* storage unavailable */ } };
  const lang = read() || ((navigator.language || 'en').toLowerCase().startsWith('nl') ? 'nl' : 'en');
  const root = document.documentElement;
  root.lang = lang;
  window.havenLang = lang;

  /* ---------- Dutch formats for dates and numbers ---------- */
  if (lang === 'nl') {
    const map = l => (typeof l === 'string' && /^en(-|$)/.test(l) && l !== 'en-CA') ? 'nl-NL' : (Array.isArray(l) && /^en(-|$)/.test(l[0]) && l[0] !== 'en-CA') ? 'nl-NL' : l;
    for (const name of ['DateTimeFormat', 'NumberFormat']) {
      const Orig = Intl[name];
      Intl[name] = new Proxy(Orig, {
        construct: (t, args) => new t(map(args[0]), ...args.slice(1)),
        apply: (t, self, args) => t(map(args[0]), ...args.slice(1))
      });
    }
    for (const [proto, names] of [[Date.prototype, ['toLocaleDateString', 'toLocaleTimeString', 'toLocaleString']], [Number.prototype, ['toLocaleString']]]) {
      for (const n of names) {
        const orig = proto[n];
        proto[n] = function (l, ...rest) { return orig.call(this, map(l), ...rest); };
      }
    }
  }

  /* ---------- translator ---------- */
  const dict = window.HAVEN_NL || {ui: {}, api: {}};
  const exact = new Map(), patterns = [];
  const squash = s => s.replace(/\s+/g, ' ').trim();
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  for (const group of [dict.ui, dict.api]) {
    for (const [en, nl] of Object.entries(group || {})) {
      if (!nl) continue;
      if (!/\{\d+\}/.test(en)) { exact.set(en, nl); continue; }
      const fixed = en.replace(/\{\d+\}/g, '');
      if (fixed.replace(/[^A-Za-z]/g, '').length < 4) continue;
      const re = new RegExp('^' + en.split(/\{\d+\}/).map(esc).join('(.*?)') + '$', 's');
      patterns.push({re, out: nl, weight: fixed.length});
    }
  }
  patterns.sort((a, b) => b.weight - a.weight);
  const cache = new Map();
  function tr(text, depth = 0) {
    if (cache.has(text)) return cache.get(text);
    let result = exact.get(text);
    if (result === undefined && depth < 3 && /[A-Za-z]{2}/.test(text)) {
      for (const p of patterns) {
        const m = p.re.exec(text);
        if (m) {
          // {n?one|many}: singular or plural wording depending on the English suffix that was captured ("", "s", "ies")
          result = p.out.replace(/\{(\d+)\?([^|{}]*)\|([^{}]*)\}/g, (_, i, one, many) => /^(s|es|ies)$/.test(m[Number(i) + 1] ?? '') ? many : one)
            .replace(/\{(\d+)\}/g, (_, i) => { const part = m[Number(i) + 1] ?? ''; return tr(squash(part), depth + 1) ?? part; });
          break;
        }
      }
    }
    if (depth === 0) cache.set(text, result ?? null);
    return result ?? null;
  }

  const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'NOSCRIPT', 'CODE', 'PRE']);
  const done = new WeakMap();
  function translateText(node) {
    const s = node.data;
    if (done.get(node) === s || !/[A-Za-z]{2}/.test(s)) return;
    const lead = s.match(/^\s*/)[0], trail = s.match(/\s*$/)[0], core = squash(s);
    const t = tr(core);
    if (t !== null && t !== core) { node.data = lead + t + trail; done.set(node, node.data); }
  }
  const ATTRS = ['placeholder', 'title', 'aria-label', 'alt'];
  function translateAttrs(el) {
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (!v || !/[A-Za-z]{2}/.test(v)) continue;
      const t = tr(squash(v));
      if (t && t !== v) el.setAttribute(a, t);
    }
  }
  function translate(node) {
    if (!node) return;
    if (node.nodeType === 3) { if (node.parentNode && !SKIP.has(node.parentNode.nodeName)) translateText(node); return; }
    if (node.nodeType !== 1 || SKIP.has(node.nodeName)) return;
    translateAttrs(node);
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: n => n.nodeType === 1 && SKIP.has(n.nodeName) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT
    });
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (n.nodeType === 3) translateText(n); else translateAttrs(n);
    }
  }

  /* ---------- language switch, always at the top ---------- */
  const label = {en: 'English', nl: 'Nederlands'};
  function makeSwitch() {
    const box = document.createElement('div');
    box.className = 'lang-switch';
    box.setAttribute('role', 'group');
    box.setAttribute('aria-label', 'Language');
    box.innerHTML = ['en', 'nl'].map(code => `<button type="button" data-lang="${code}" class="${code === lang ? 'active' : ''}" aria-pressed="${code === lang}" title="${label[code]}" lang="${code}">${code.toUpperCase()}</button>`).join('');
    return box;
  }
  function placeSwitch() {
    const bar = document.querySelector('.topbar');
    let box = document.querySelector('.lang-switch');
    if (!box) { box = makeSwitch(); }
    if (bar) {
      if (box.parentNode !== bar) bar.append(box);
      box.classList.remove('floating');
    } else if (!box.classList.contains('floating') || box.parentNode !== document.body) {
      box.classList.add('floating');
      document.body.append(box);
    }
  }
  document.addEventListener('click', ev => {
    const b = ev.target.closest && ev.target.closest('.lang-switch [data-lang]');
    if (!b || b.dataset.lang === lang) return;
    save(b.dataset.lang);
    location.reload();  // every screen redraws in the chosen language
  });

  let pending = new Set(), queued = false;
  function flush() {
    queued = false;
    placeSwitch();
    if (lang !== 'nl') { pending.clear(); return; }
    const batch = [...pending]; pending.clear();
    for (const n of batch) if (n.isConnected) translate(n);
  }
  function schedule() { if (!queued) { queued = true; requestAnimationFrame(flush); } }
  new MutationObserver(list => {
    for (const m of list) {
      if (m.type === 'childList') m.addedNodes.forEach(n => pending.add(n));
      else if (m.type === 'characterData') pending.add(m.target);
      else if (m.type === 'attributes') pending.add(m.target);
    }
    schedule();
  }).observe(document.documentElement, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS});

  document.addEventListener('DOMContentLoaded', () => { pending.add(document.body); schedule(); });
  if (document.readyState !== 'loading') { pending.add(document.body); schedule(); }
  window.havenTranslate = translate;
  window.havenT = (text) => (lang === 'nl' && tr(squash(String(text)))) || text;
})();
