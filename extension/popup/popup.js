import {
  loadSettings,
  checkHealth,
  sendClip,
  previewClip,
  fallbackFile,
  safeFilename
} from '../lib/client.js';

const $ = (id) => document.getElementById(id);

const els = {
  loading: $('loading'),
  error: $('error'),
  form: $('form'),
  title: $('title'),
  tags: $('tags'),
  filedAs: $('filedAs'),
  filedWhy: $('filedWhy'),
  filename: $('filename'),
  preview: $('previewText'),
  stats: $('stats'),
  dest: $('dest'),
  status: $('status'),
  save: $('save'),
  dot: $('serverDot'),
  options: $('openOptions')
};

const state = {
  settings: null,
  tab: null,
  mode: 'article',
  extracted: null,  // { html, url, mode } captured from the tab
  preview: null,    // the server's /preview response
  health: null,
  busy: false
};

// ---------------------------------------------------------------------------

function setStatus(text, tone) {
  els.status.textContent = text || '';
  if (tone) els.status.dataset.tone = tone;
  else delete els.status.dataset.tone;
}

function showError(message) {
  els.loading.hidden = true;
  els.form.hidden = true;
  els.error.hidden = false;
  els.error.textContent = message;
  els.save.disabled = true;
}

function isRestricted(url) {
  return (
    !url ||
    /^(chrome|edge|about|devtools|view-source|chrome-extension|moz-extension):/i.test(url) ||
    url.startsWith('https://chromewebstore.google.com') ||
    url.startsWith('https://chrome.google.com/webstore')
  );
}

// ---------------------------------------------------------------------------
// Server status
// ---------------------------------------------------------------------------

async function refreshHealth() {
  els.dot.dataset.state = 'checking';
  els.dot.title = 'Checking clip server…';

  const health = await checkHealth(state.settings);
  state.health = health;

  if (health.up && health.writable) {
    els.dot.dataset.state = 'up';
    els.dot.title = `Clip server up · vault ${health.vault} · ${health.rules} site rules`;
  } else if (health.up) {
    els.dot.dataset.state = 'down';
    els.dot.title = `Clip server up but the vault is not writable (${health.vault})`;
  } else {
    els.dot.dataset.state = 'down';
    els.dot.title = `Clip server unreachable: ${health.reason}`;
  }

  refreshDest();
}

function refreshDest() {
  const h = state.health;
  const name = els.filename.value.trim();

  els.dest.replaceChildren();

  if (h?.up && h.writable) {
    // Every clip lands in the same folder now. What it is *about* is recorded
    // in the note's frontmatter instead, so there is one path to show.
    const bits = [h.vault];
    const folder = state.preview?.subfolder || '';
    if (folder) bits.push(folder);
    els.dest.append(bits.join(' / ') + ' / ');

    const stem = name || state.preview?.filename || '';
    const b = document.createElement('b');
    b.textContent = stem ? safeFilename(stem) + '.md' : '(server decides)';
    els.dest.append(b);
  } else if (h?.up) {
    els.dest.append('Server is up but cannot write to ' + h.vault);
  } else if (state.settings.fallbackToDownloads) {
    els.dest.append('Server down — will save to Downloads/' + state.settings.downloadsSubfolder);
  } else {
    els.dest.append('Server down. Start the container, or turn on the Downloads fallback.');
  }
}

// ---------------------------------------------------------------------------
// Extraction
// ---------------------------------------------------------------------------

async function extract(mode) {
  els.error.hidden = true;
  els.form.hidden = true;
  els.loading.hidden = false;
  els.loading.textContent = mode === 'page' ? 'Reading the whole page…' : 'Reading the page…';
  els.save.disabled = true;
  state.preview = null;

  try {
    await chrome.scripting.executeScript({
      target: { tabId: state.tab.id },
      files: ['content/extract.js']
    });
  } catch {
    showError('Chrome will not let the extension run on this page. Open a normal web page and try again.');
    return;
  }

  let results;
  try {
    results = await chrome.scripting.executeScript({
      target: { tabId: state.tab.id },
      func: (opts) => globalThis.__clipvault_extract(opts),
      args: [{ mode }]
    });
  } catch (e) {
    showError('Extraction failed: ' + (e?.message || String(e)));
    return;
  }

  const data = results?.[0]?.result;
  if (!data) {
    showError('The page returned nothing. Reload the tab and try again.');
    return;
  }
  if (data.ok === false) {
    showError(data.error);
    return;
  }

  state.extracted = data;

  // The extractor lives on the server now, so the note has to be fetched
  // rather than built here.
  els.loading.textContent = 'Extracting…';

  try {
    state.preview = await previewClip(state.settings, buildPayload());
  } catch (e) {
    if (e.unreachable) {
      // The HTML is captured either way, so let the user save it through the
      // Downloads fallback instead of blocking on a preview we cannot get.
      els.dot.dataset.state = 'down';
      els.loading.hidden = true;
      els.form.hidden = false;
      els.title.value = '';
      els.preview.textContent = '';
      refreshFiledAs();
      updateSaveState();
      els.stats.textContent = `${Math.round(data.html.length / 1024)} KB of HTML captured`;
      setStatus('Clip server is down — no preview. Saving will keep the raw HTML.');
      refreshDest();
      return;
    }
    showError(e?.message || String(e));
    return;
  }

  els.loading.hidden = true;
  els.form.hidden = false;
  els.title.value = state.preview.title || '';
  updateSaveState();

  // Surface anything other than the normal path — a whole-page extraction is
  // worth eyeballing in the preview.
  const strategy = state.preview.strategy;
  setStatus(strategy && strategy !== 'trafilatura (article)' ? `Extracted via ${strategy}` : '');

  refreshPreview();
}

function refreshPreview() {
  const p = state.preview;
  if (!p) return;
  refreshFiledAs();
  els.preview.textContent = p.markdown;
  els.stats.textContent = `${p.word_count.toLocaleString()} words · ${Math.round(
    p.markdown.length / 1024
  )} KB`;
  refreshDest();
}

// ---------------------------------------------------------------------------
// Sending
// ---------------------------------------------------------------------------

// --- category / subcategory -------------------------------------------------
// Nothing is picked here. The server derives both from the category the page
// publishes for itself plus the matching rule in config.yaml, so the popup only
// reports what it decided — and never blocks Save on it.

/** Show the filing the server worked out, and where it came from. */
function refreshFiledAs() {
  const p = state.preview;

  if (!p) {
    els.filedAs.textContent = '—';
    els.filedWhy.textContent = '';
    delete els.filedAs.dataset.tone;
    return;
  }

  const parts = [p.category, p.subcategory].filter(Boolean);
  els.filedAs.textContent = parts.length ? parts.join(' › ') : 'nothing';
  els.filedAs.dataset.tone = parts.length ? 'set' : 'empty';

  if (p.site_category) {
    els.filedWhy.textContent = `the page says "${p.site_category}"`;
  } else if (parts.length) {
    els.filedWhy.textContent = 'from your site rule';
  } else {
    els.filedWhy.textContent = 'this site declares no category';
  }
}

/** Save waits only on having something to send. */
function updateSaveState() {
  els.save.disabled = !state.extracted || state.busy;
}

function buildPayload() {
  const d = state.extracted;

  // title goes out empty unless the user edited it, so the server's extracted
  // title wins by default. category/subcategory are not sent at all — they are
  // the server's to decide.
  const payload = {
    html: d.html,
    url: d.url,
    mode: d.mode,
    title: els.title.value || '',
    tags: els.tags.value.split(',').map((t) => t.trim()).filter(Boolean)
  };

  const name = els.filename.value.trim();
  if (name) payload.filename = name;

  return payload;
}

async function saveToDownloads(payload) {
  const file = fallbackFile(payload);
  const blob = new Blob([file.contents], { type: file.type });
  const url = URL.createObjectURL(blob);
  const path = `${state.settings.downloadsSubfolder}/${safeFilename(
    els.title.value || 'clipped-page'
  )}${file.extension}`;

  const id = await chrome.downloads.download({
    url,
    filename: path,
    conflictAction: 'uniquify',
    saveAs: false
  });

  await new Promise((resolve) => {
    const done = (delta) => {
      if (delta.id === id && delta.state?.current === 'complete') {
        chrome.downloads.onChanged.removeListener(done);
        resolve();
      }
    };
    chrome.downloads.onChanged.addListener(done);
    setTimeout(resolve, 4000);
  });

  URL.revokeObjectURL(url);
  return path;
}

async function save() {
  if (state.busy || !state.extracted) return;
  state.busy = true;
  els.save.disabled = true;
  setStatus('Sending…');

  const payload = buildPayload();

  try {
    const result = await sendClip(state.settings, payload);
    const assets =
      result.assets_found > 0
        ? ` · ${result.assets_saved}/${result.assets_found} images`
        : '';
    const filed = [result.category, result.subcategory].filter(Boolean).join(' › ');
    setStatus(`Saved ${result.path}${assets}${filed ? ` · ${filed}` : ''}`, 'ok');
    els.dot.dataset.state = 'up';
  } catch (e) {
    if (e.unreachable && state.settings.fallbackToDownloads) {
      els.dot.dataset.state = 'down';
      try {
        const path = await saveToDownloads(payload);
        setStatus(`Server down — saved raw HTML to Downloads/${path}`, 'ok');
      } catch (inner) {
        setStatus('Both routes failed: ' + (inner?.message || String(inner)), 'err');
      }
    } else {
      if (e.unreachable) els.dot.dataset.state = 'down';
      setStatus(e?.message || String(e), 'err');
    }
  } finally {
    state.busy = false;
    updateSaveState();
  }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function setMode(mode, reExtract = true) {
  state.mode = mode;
  document.querySelectorAll('.modes__btn').forEach((btn) => {
    btn.setAttribute('aria-pressed', String(btn.dataset.mode === mode));
  });
  if (reExtract) extract(mode);
}

async function init() {
  state.settings = await loadSettings();
  els.tags.value = state.settings.defaultTags;

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  state.tab = tab;

  refreshHealth();

  if (!tab || isRestricted(tab.url)) {
    showError('This page is off limits to extensions. Open a normal web page and try again.');
    return;
  }

  setMode(state.settings.defaultMode === 'page' ? 'page' : 'article', false);
  await extract(state.mode);
}

document.querySelectorAll('.modes__btn').forEach((btn) => {
  btn.addEventListener('click', () => setMode(btn.dataset.mode));
});

['input', 'change'].forEach((evt) => {
  els.filename.addEventListener(evt, refreshDest);
});

els.save.addEventListener('click', save);
els.options.addEventListener('click', () => chrome.runtime.openOptionsPage());

document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
    e.preventDefault();
    save();
  }
});

init();
