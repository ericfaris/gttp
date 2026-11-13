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
  var link = document.getElementById('link');
  var title = document.getElementById('title');
  var author = document.getElementById('author');
  var checkBtn = document.getElementById('check-btn');
  var submitBtn = document.getElementById('submit-btn');
  var preview = document.getElementById('check-preview');
  var sourceLine = document.getElementById('check-source');
  var changeList = document.getElementById('check-changes');
  var warnList = document.getElementById('check-warnings');
  var dupLine = document.getElementById('check-duplicate');
  var errBox = document.getElementById('form-error');
  var okBox = document.getElementById('form-ok');
  var list = document.getElementById('queue');
  var empty = document.getElementById('queue-empty');
  var limits = document.getElementById('limits');
  var timer = null;

  var LABELS = { queued: 'Queued', running: 'Building', done: 'Published', failed: 'Failed' };
  var REASONS = { capitalization: 'capitalization', 'open-library': 'Open Library', link: 'from the link' };
  // The last successful check: {title, author, ready, source}. Queue it is
  // enabled only while the inputs still equal what was checked.
  var checked = null;

  function showError(msg) {
    okBox.textContent = '';
    errBox.textContent = msg || '';
    title.setAttribute('aria-invalid', /^title\b|^'/.test(msg || '') ? 'true' : 'false');
    author.setAttribute('aria-invalid', /^author\b/.test(msg || '') ? 'true' : 'false');
    link.setAttribute('aria-invalid', /^link\b/.test(msg || '') ? 'true' : 'false');
  }

  function matches() {
    return !!checked && title.value.trim() === checked.title && author.value.trim() === checked.author;
  }

  function sourceText() {
    return checked.source === 'link' ? 'Filled in from the link.' : 'Checked what you typed.';
  }

  function syncQueue() {
    submitBtn.disabled = !(checked && checked.ready && matches());
    var stale = !!checked && !matches();
    preview.classList.toggle('is-stale', stale);
    if (checked) sourceLine.textContent = stale ? 'Edited: press Check again.' : sourceText();
  }

  function clearPreview() {
    [changeList, warnList].forEach(function (n) { while (n.firstChild) n.removeChild(n.firstChild); });
    dupLine.textContent = '';
    sourceLine.textContent = '';
    preview.classList.remove('is-stale');
    preview.hidden = true;
  }

  function changeItem(c) {
    var li = el('li', 'change-item');
    li.appendChild(el('span', 'change-field', c.field === 'author' ? 'Author' : 'Title'));
    li.appendChild(c.from ? el('del', 'change-from', c.from) : el('del', 'change-from is-empty', '(empty)'));
    var arrow = el('span', 'change-arrow', '\u2192');
    arrow.setAttribute('aria-hidden', 'true');
    li.appendChild(arrow);
    li.appendChild(el('ins', 'change-to', c.to));
    li.appendChild(el('span', 'tag change-reason', REASONS[c.reason] || c.reason));
    return li;
  }

  function showCheck(data) {
    title.value = data.title || '';
    author.value = data.author || '';
    checked = { title: title.value, author: author.value, ready: data.ready === true, source: data.source };
    clearPreview();
    (data.changes || []).forEach(function (c) { changeList.appendChild(changeItem(c)); });
    (data.warnings || []).forEach(function (w) { warnList.appendChild(el('li', 'form-warn', w)); });
    dupLine.textContent = data.duplicate || '';
    preview.hidden = false;
    syncQueue();
    if (!submitBtn.disabled) submitBtn.focus();
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
    if (l) limits.textContent = l.today + ' of ' + l.daily_cap + ' today \u00b7 ' +
      l.checks_today + ' of ' + l.check_cap + ' lookups';
    return entries.some(function (e) { return e.state === 'queued' || e.state === 'running'; });
  }

  function poll() {
    clearTimeout(timer);
    fetch('/api/status', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (data) { timer = setTimeout(poll, render(data) ? 10000 : 60000); })
      .catch(function () { timer = setTimeout(poll, 60000); });
  }

  [link, title, author].forEach(function (n) { n.addEventListener('input', syncQueue); });

  // Enter or the Check button: look the book up. Never queues anything.
  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var body = {};
    if (link.value.trim()) body.url = link.value.trim();
    if (title.value.trim()) body.title = title.value.trim();
    if (author.value.trim()) body.author = author.value.trim();
    checkBtn.disabled = true;
    checkBtn.textContent = 'Checking\u2026';
    showError('');
    fetch('/api/check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok || !res.data.ok) {
          showError(res.data.error || 'Something went wrong.');
          syncQueue();
          return;
        }
        showCheck(res.data);
      })
      .catch(function () { showError("Couldn't reach the submit service. Try again in a minute."); syncQueue(); })
      .then(function () { checkBtn.disabled = false; checkBtn.textContent = 'Check'; });
  });

  submitBtn.addEventListener('click', function () {
    if (submitBtn.disabled || !checked) return;
    var body = JSON.stringify({ title: checked.title, author: checked.author });
    submitBtn.disabled = true;
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
        checked = null;
        clearPreview();
        poll();
      })
      .catch(function () { showError("Couldn't reach the submit service. Try again in a minute."); })
      .then(function () { syncQueue(); });
  });

  poll();
});
