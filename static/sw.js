'use strict';
const CACHE = 'haven-shell-v30';
const SHELL = ['/', '/static/index.html', '/static/styles.css', '/static/portal.css', '/static/enhance.css', '/static/polish.css', '/static/invoices.css', '/static/signature.css', '/static/docedit.css', '/static/pdfview.css', '/static/theme.css', '/static/favicon.svg'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL).catch(() => {})).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const req = event.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  // Network first so deployments show immediately; the cache is only an offline fallback.
  event.respondWith(fetch(req).then(res => {
    if (res.ok && (url.pathname.startsWith('/static/') || req.mode === 'navigate')) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
    return res;
  }).catch(() => caches.match(req).then(hit => hit || caches.match('/'))));
});
