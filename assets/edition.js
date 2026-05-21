// edition.js — minimal interactivity
(function () {
  'use strict';

  // --- Footnote popovers -----------------------------------
  // Each .fn-ref points to #fn-N; show a tooltip on hover.
  document.querySelectorAll('.fn-ref').forEach(function (ref) {
    var targetId = (ref.getAttribute('href') || '').replace('#', '');
    if (!targetId) return;
    var note = document.getElementById(targetId);
    if (!note) return;

    var pop = document.createElement('span');
    pop.className = 'fn-pop';
    pop.style.cssText = [
      'position:absolute',
      'visibility:hidden',
      'opacity:0',
      'pointer-events:none',
      'max-width:32ch',
      'background:var(--paper)',
      'border:1px solid var(--rule)',
      'box-shadow:0 6px 24px var(--shadow)',
      'padding:0.7em 0.9em',
      'font-family:var(--body)',
      'font-size:0.86rem',
      'line-height:1.5',
      'color:var(--ink-soft)',
      'z-index:60',
      'transition:opacity 160ms ease',
      'border-radius:2px'
    ].join(';');
    pop.textContent = note.textContent.replace(/^\s*\d+\s*/, '').trim();
    document.body.appendChild(pop);

    ref.addEventListener('mouseenter', function () {
      var rect = ref.getBoundingClientRect();
      pop.style.left = (window.scrollX + rect.left) + 'px';
      pop.style.top = (window.scrollY + rect.bottom + 6) + 'px';
      pop.style.visibility = 'visible';
      pop.style.opacity = '1';
    });
    ref.addEventListener('mouseleave', function () {
      pop.style.opacity = '0';
      setTimeout(function () { pop.style.visibility = 'hidden'; }, 180);
    });
  });

  // --- Keyboard nav on page-nav: ← / → ---------------------
  document.addEventListener('keydown', function (e) {
    if (e.target && /input|textarea/i.test(e.target.tagName)) return;
    var prev = document.querySelector('.page-nav a[rel="prev"]');
    var next = document.querySelector('.page-nav a[rel="next"]');
    if (e.key === 'ArrowLeft' && prev) { window.location.href = prev.href; }
    else if (e.key === 'ArrowRight' && next) { window.location.href = next.href; }
  });

  // --- Region cross-highlight on facsimile pages ------------
  // Hovering a transcription region briefly shows where it sits.
  document.querySelectorAll('.region[data-bbox]').forEach(function (r) {
    r.addEventListener('mouseenter', function () {
      r.style.boxShadow = 'inset 3px 0 0 var(--accent)';
    });
    r.addEventListener('mouseleave', function () {
      r.style.boxShadow = 'none';
    });
  });

  // --- Layout-region toggle on the facsimile ----------------
  document.querySelectorAll('.region-toggle').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var stage = btn.closest('.facsimile-stage');
      if (!stage) return;
      var on = stage.dataset.showRegions === 'on';
      stage.dataset.showRegions = on ? 'off' : 'on';
      btn.setAttribute('aria-pressed', on ? 'false' : 'true');
      btn.textContent = on ? 'Show regions' : 'Hide regions';
    });
  });

  // --- Click a region box to jump to its transcription block
  document.querySelectorAll('.region-box').forEach(function (box) {
    box.addEventListener('click', function (e) {
      var id = box.getAttribute('data-id');
      if (!id) return;
      var target = document.getElementById(id);
      if (!target) return;
      e.preventDefault();
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      target.classList.add('region-flash');
      setTimeout(function () { target.classList.remove('region-flash'); }, 1100);
    });
  });
})();
