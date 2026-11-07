// gttp submit page. Loaded synchronously in <head> (the CSP allows no
// inline script), so the theme is applied before first paint; everything
// else waits for DOMContentLoaded. Untrusted text (titles, authors, worker
// messages) only ever goes in through textContent, never parsed as HTML.

(function () {
  try {
    var t = localStorage.getItem('gttp-theme');
    if (t) document.documentElement.setAttribute('data-theme', t);
  } catch (e) {}
})();

document.addEventListener('DOMContentLoaded', function () {
  // Theme toggle (same behaviour as the public site).
  var toggle = document.getElementById('theme-toggle');
  function cur() {
    var a = document.documentElement.getAttribute('data-theme');
    if (a) return a;
    return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }
  function sync() { toggle.setAttribute('aria-pressed', cur() === 'dark'); }
  sync();
  toggle.addEventListener('click', function () {
    var n = cur() === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', n);
    try { localStorage.setItem('gttp-theme', n); } catch (e) {}
    sync();
  });

  var form = document.getElementById('submit-form');
  var title = document.getElementById('title');
  var author = document.getElementById('author');
  var btn = document.getElementById('submit-btn');
  var errBox = document.getElementById('form-error');
  var okBox = document.getElementById('form-ok');
  var list = document.getElementById('queue');
  var empty = document.getElementById('queue-empty');
  var limits = document.getElementById('limits');
  var timer = null;

  var LABELS = { queued: 'Queued', running: 'Building', done: 'Published', failed: 'Failed' };

  function showError(msg) {
    okBox.textContent = '';
    errBox.textContent = msg || '';
    title.setAttribute('aria-invalid', /^title\b|^'/.test(msg || '') ? 'true' : 'false');
    author.setAttribute('aria-invalid', /^author\b/.test(msg || '') ? 'true' : 'false');
  }

  function when(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function render(data) {
    var entries = data.entries || [];
    while (list.firstChild) list.removeChild(list.firstChild);
    entries.forEach(function (e) {
      var state = LABELS[e.state] ? e.state : 'queued';
      var li = el('li', 'queue-item');
      var head = el('div', 'queue-head');
      head.appendChild(el('span', 'queue-title', e.title));
      head.appendChild(el('span', 'state-pill state-' + state, LABELS[state]));
      li.appendChild(head);
      li.appendChild(el('p', 'queue-author', 'by ' + e.author));
      if (e.message) li.appendChild(el('p', 'queue-message', e.message));
      var meta = when(e.created_at);
      if (e.commit) meta += ' · commit ' + e.commit;
      if (typeof e.usd === 'number') meta += ' · $' + e.usd.toFixed(2);
      li.appendChild(el('p', 'queue-meta', meta));
      list.appendChild(li);
    });
    empty.hidden = entries.length > 0;
    var l = data.limits;
    if (l) limits.textContent = l.today + ' of ' + l.daily_cap + ' today';
    return entries.some(function (e) { return e.state === 'queued' || e.state === 'running'; });
  }

  function poll() {
    clearTimeout(timer);
    fetch('/api/status', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (data) { timer = setTimeout(poll, render(data) ? 10000 : 60000); })
      .catch(function () { timer = setTimeout(poll, 60000); });
  }

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var body = JSON.stringify({ title: title.value.trim(), author: author.value.trim() });
    btn.disabled = true;
    showError('');
    fetch('/api/submit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok || !res.data.ok) {
          showError(res.data.error || 'Something went wrong.');
          return;
        }
        okBox.textContent = 'Queued "' + res.data.entry.title + '". It will show below as it builds.';
        form.reset();
        poll();
      })
      .catch(function () { showError("Couldn't reach the submit service. Try again in a minute."); })
      .then(function () { btn.disabled = false; });
  });

  poll();
});
