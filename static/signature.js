'use strict';
/* Signature pad with three ways to sign: draw (mouse, finger, stylus), type (handwriting-style fonts) and upload (photo or scan).
   Shared by the tenant portal and the landlord screen. Produces a small transparent PNG data URL.
   HavenSignature.html(name)  -> markup;  HavenSignature.bind(root, name, onChange) -> {ready(), data(), mode()}  */
(() => {
  const FONTS = [['Dancing Script', 'Flowing'], ['Caveat', 'Casual'], ['Great Vibes', 'Elegant']];
  const INK = '#14213d';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

  function html(name) {
    return `<div class="sig" data-sig>
      <div class="sig-tabs" role="tablist" aria-label="How do you want to sign?">
        <button type="button" role="tab" class="active" data-sig-tab="draw" aria-selected="true">Draw</button>
        <button type="button" role="tab" data-sig-tab="type" aria-selected="false">Type</button>
        <button type="button" role="tab" data-sig-tab="upload" aria-selected="false">Upload</button></div>
      <div class="sig-pane" data-sig-pane="draw"><div class="sig-board"><canvas class="sig-canvas" data-sig-canvas aria-label="Signature pad: draw your signature here"></canvas><span class="sig-line"></span><span class="sig-x">✕</span></div>
        <div class="sig-tools"><span>Sign above the line with your mouse, finger or stylus.</span><button type="button" class="button small" data-sig-clear>Clear</button></div></div>
      <div class="sig-pane hidden" data-sig-pane="type"><label class="sig-label" for="sig-name-input">Your name as it should appear</label><input id="sig-name-input" class="sig-name" data-sig-name type="text" maxlength="60" value="${esc(name)}" autocomplete="off">
        <div class="sig-styles">${FONTS.map(([f, label], i) => `<button type="button" class="sig-style ${i === 0 ? 'active' : ''}" data-sig-style="${i}" aria-label="${label} style"><span class="sig-font-${i}" data-sig-sample>${esc(name)}</span><small>${label}</small></button>`).join('')}</div></div>
      <div class="sig-pane hidden" data-sig-pane="upload"><label class="button sig-pick">Choose a photo or scan<input type="file" data-sig-file accept="image/png,image/jpeg" hidden></label>
        <div class="sig-preview" data-sig-preview><span>Sign with a dark pen on white paper, then take a clear photo. The background is removed automatically.</span></div></div>
    </div>`;
  }

  function bind(root, name, onChange = () => {}) {
    const box = root.querySelector('[data-sig]');
    const canvas = box.querySelector('[data-sig-canvas]'), ctx = canvas.getContext('2d');
    const state = {mode: 'draw', inked: 0, strokes: 0, style: 0, text: name || '', upload: null};
    const fire = () => onChange();

    // ----- draw
    const fit = () => {
      const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      if (!r.width) return;
      const keep = state.inked ? ctx.getImageData(0, 0, canvas.width, canvas.height) : null;
      canvas.width = Math.round(r.width * dpr); canvas.height = Math.round(r.height * dpr);
      ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.strokeStyle = INK; ctx.lineWidth = 2.6 * dpr;
      if (keep) ctx.putImageData(keep, 0, 0);
    };
    let last = null, drawing = false;
    const pos = ev => { const r = canvas.getBoundingClientRect(), dpr = canvas.width / r.width; return [(ev.clientX - r.left) * dpr, (ev.clientY - r.top) * dpr]; };
    canvas.addEventListener('pointerdown', ev => {
      ev.preventDefault(); fit();
      canvas.setPointerCapture(ev.pointerId); drawing = true; last = pos(ev);
      ctx.beginPath(); ctx.arc(last[0], last[1], ctx.lineWidth / 2, 0, 6.3); ctx.fillStyle = INK; ctx.fill();
      state.strokes++; state.inked += 6;
    });
    canvas.addEventListener('pointermove', ev => {
      if (!drawing) return;
      const p = pos(ev), pressure = ev.pressure && ev.pointerType === 'pen' ? 0.6 + ev.pressure : 1;
      ctx.lineWidth = 2.6 * (canvas.width / canvas.getBoundingClientRect().width) * pressure;
      ctx.beginPath(); ctx.moveTo(last[0], last[1]); ctx.lineTo(p[0], p[1]); ctx.stroke();
      state.inked += Math.hypot(p[0] - last[0], p[1] - last[1]); last = p; fire();
    });
    const stop = () => { if (drawing) { drawing = false; fire(); } };
    canvas.addEventListener('pointerup', stop); canvas.addEventListener('pointercancel', stop);
    box.querySelector('[data-sig-clear]').addEventListener('click', () => { ctx.clearRect(0, 0, canvas.width, canvas.height); state.inked = 0; state.strokes = 0; fire(); });

    // ----- tabs
    const show = mode => {
      state.mode = mode;
      box.querySelectorAll('[data-sig-tab]').forEach(b => { const on = b.dataset.sigTab === mode; b.classList.toggle('active', on); b.setAttribute('aria-selected', on ? 'true' : 'false'); });
      box.querySelectorAll('[data-sig-pane]').forEach(p => p.classList.toggle('hidden', p.dataset.sigPane !== mode));
      if (mode === 'draw') fit();
      fire();
    };
    box.querySelectorAll('[data-sig-tab]').forEach(b => b.addEventListener('click', () => show(b.dataset.sigTab)));

    // ----- type
    const nameInput = box.querySelector('[data-sig-name]');
    nameInput.addEventListener('input', () => { state.text = nameInput.value; box.querySelectorAll('[data-sig-sample]').forEach(s => { s.textContent = nameInput.value || '…'; }); fire(); });
    box.querySelectorAll('[data-sig-style]').forEach(b => b.addEventListener('click', () => { state.style = Number(b.dataset.sigStyle); box.querySelectorAll('[data-sig-style]').forEach(x => x.classList.toggle('active', x === b)); fire(); }));

    // ----- upload
    const fileInput = box.querySelector('[data-sig-file]'), preview = box.querySelector('[data-sig-preview]');
    fileInput.addEventListener('change', () => {
      const f = fileInput.files[0]; if (!f) return;
      if (f.size > 4 * 1024 * 1024) { preview.textContent = 'That image is too large (4 MB at most).'; state.upload = null; fire(); return; }
      const reader = new FileReader();   // a data URL (the page's security policy does not allow blob: images)
      reader.onerror = () => { preview.textContent = 'That file could not be read.'; state.upload = null; fire(); };
      reader.onload = () => {
        const img = new Image();
        img.onload = () => {
          const scale = Math.min(1, 720 / img.width, 240 / img.height), c = document.createElement('canvas');
          c.width = Math.max(20, Math.round(img.width * scale)); c.height = Math.max(10, Math.round(img.height * scale));
          const g = c.getContext('2d'); g.drawImage(img, 0, 0, c.width, c.height);
          const d = g.getImageData(0, 0, c.width, c.height), px = d.data;
          for (let i = 0; i < px.length; i += 4) {   // paper white becomes transparent, the pen stays
            const lum = 0.3 * px[i] + 0.59 * px[i + 1] + 0.11 * px[i + 2];
            if (lum > 215) px[i + 3] = 0; else { px[i] = 20; px[i + 1] = 33; px[i + 2] = 61; px[i + 3] = Math.min(255, Math.round((215 - lum) * 2.2)); }
          }
          g.putImageData(d, 0, 0); state.upload = c;
          preview.innerHTML = ''; const out = document.createElement('img'); out.alt = 'Your uploaded signature'; out.src = trim(c).toDataURL('image/png'); preview.appendChild(out); fire();
        };
        img.onerror = () => { preview.textContent = 'That file could not be read as an image.'; state.upload = null; fire(); };
        img.src = reader.result;
      };
      reader.readAsDataURL(f);
    });

    // ----- output
    function trim(src) {
      const g = src.getContext('2d'), w = src.width, h = src.height, px = g.getImageData(0, 0, w, h).data;
      let x0 = w, y0 = h, x1 = 0, y1 = 0;
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (px[(y * w + x) * 4 + 3] > 12) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
      if (x1 < x0) return src;
      const pad = 8, c = document.createElement('canvas'); c.width = Math.min(1400, x1 - x0 + 1 + pad * 2); c.height = y1 - y0 + 1 + pad * 2;
      const scale = c.width / (x1 - x0 + 1 + pad * 2); if (scale < 1) c.height = Math.round(c.height * scale);
      c.getContext('2d').drawImage(src, x0 - pad, y0 - pad, x1 - x0 + 1 + pad * 2, y1 - y0 + 1 + pad * 2, 0, 0, c.width, c.height);
      return c;
    }
    const typedCanvas = async () => {
      const [family] = FONTS[state.style], text = state.text.trim(), size = 110;
      try { await document.fonts.load(`${size}px "${family}"`, text); } catch { /* falls back to a cursive system font */ }
      const c = document.createElement('canvas'), g = c.getContext('2d'), font = `${size}px "${family}", "Segoe Script", "Brush Script MT", cursive`;
      g.font = font; c.width = Math.ceil(g.measureText(text).width) + 60; c.height = size * 2;
      const g2 = c.getContext('2d'); g2.font = font; g2.fillStyle = INK; g2.textBaseline = 'alphabetic'; g2.fillText(text, 30, size * 1.3);
      return c;
    };
    return {
      mode: () => state.mode,
      ready: () => state.mode === 'draw' ? state.inked > 40 && state.strokes > 0 : state.mode === 'type' ? state.text.trim().length >= 2 : !!state.upload,
      async data() {
        if (!this.ready()) return null;
        const src = state.mode === 'draw' ? canvas : state.mode === 'type' ? await typedCanvas() : state.upload;
        return {dataUrl: trim(src).toDataURL('image/png'), mode: state.mode};
      },
      refit: fit
    };
  }
  window.HavenSignature = {html, bind};
})();
