import { DEFAULTS, loadSettings, saveSettings, checkHealth } from '../lib/client.js';

const $ = (id) => document.getElementById(id);

function setStatus(el, text, tone) {
  el.textContent = text || '';
  if (tone) el.dataset.tone = tone;
  else delete el.dataset.tone;
}

function currentSettings() {
  return {
    serverUrl: $('serverUrl').value.trim() || DEFAULTS.serverUrl,
    token: $('token').value,
    defaultMode: $('defaultMode').value,
    defaultTags: $('defaultTags').value.trim(),
    fallbackToDownloads: $('fallbackToDownloads').checked,
    downloadsSubfolder: $('downloadsSubfolder').value.trim() || DEFAULTS.downloadsSubfolder
  };
}

async function load() {
  const s = await loadSettings();
  $('serverUrl').value = s.serverUrl;
  $('token').value = s.token;
  $('defaultMode').value = s.defaultMode;
  $('defaultTags').value = s.defaultTags;
  $('fallbackToDownloads').checked = s.fallbackToDownloads;
  $('downloadsSubfolder').value = s.downloadsSubfolder;
  test();
}

async function save() {
  // Merge rather than replace, so a setting this form does not render is kept.
  const existing = await loadSettings();
  await saveSettings({ ...existing, ...currentSettings() });
  const el = $('saveStatus');
  setStatus(el, 'Settings saved.', 'ok');
  setTimeout(() => setStatus(el, ''), 3000);
}

async function test() {
  const el = $('testResult');
  setStatus(el, 'Checking…');

  const health = await checkHealth(currentSettings(), 3000);

  if (!health.up) {
    setStatus(
      el,
      `Can't reach it (${health.reason}). Is the container running? Try: docker compose up -d`,
      'err'
    );
    return;
  }
  if (!health.writable) {
    setStatus(el, `Connected, but ${health.vault} is not writable. Check VAULT_PATH and PUID/PGID.`, 'err');
    return;
  }
  if (health.configError) {
    setStatus(el, `Connected. Config problem: ${health.configError}`, 'err');
    return;
  }

  const auth = health.authRequired ? 'token required' : 'no token';
  setStatus(el, `Connected · vault ${health.vault} · ${health.rules} site rules · ${auth}`, 'ok');
}

$('save').addEventListener('click', save);
$('testConn').addEventListener('click', test);

$('toggleToken').addEventListener('click', () => {
  const input = $('token');
  const showing = input.type === 'text';
  input.type = showing ? 'password' : 'text';
  $('toggleToken').textContent = showing ? 'Show' : 'Hide';
});

document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 's') {
    e.preventDefault();
    save();
  }
});

load();
