'use strict';
// Installable app shell. API responses are never cached, so financial data is always live.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js', {scope: '/'}).catch(() => {}); });
}
