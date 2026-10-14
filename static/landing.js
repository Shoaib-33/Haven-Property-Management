'use strict';
(() => {
  const KEY = 'haven_lang';
  const read = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
  const write = v => { try { localStorage.setItem(KEY, v); } catch { /* storage unavailable */ } };
  let lang = read() || ((navigator.language || 'nl').toLowerCase().startsWith('nl') ? 'nl' : 'en');
  const apply = () => {
    document.documentElement.lang = lang;
    document.querySelectorAll('[data-nl]').forEach(el => { el.textContent = el.dataset[lang]; });
    const b = document.getElementById('lang');
    if (b) b.textContent = lang === 'nl' ? 'EN' : 'NL';
  };
  document.getElementById('lang')?.addEventListener('click', () => { lang = lang === 'nl' ? 'en' : 'nl'; write(lang); apply(); });
  apply();
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(entries => entries.forEach(en => { if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); } }), {threshold: .15});
    document.querySelectorAll('.card,.feature,.steps li,.chips li,.band h2').forEach(el => { el.classList.add('reveal'); io.observe(el); });
  }
})();
