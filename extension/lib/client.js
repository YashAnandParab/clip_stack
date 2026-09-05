/*
 * client.js — settings plus everything that talks to the clip server.
 *
 * The extension is deliberately thin. It reads the page and posts JSON.
 * Templates, file naming, per-site routing and image handling all live in
 * Python, where they are easier to change.
 */

export const DEFAULTS = {
  serverUrl: 'http://127.0.0.1:8765',
  token: '',
  defaultTags: '',
  fallbackToDownloads: true,
  downloadsSubfolder: 'Clips'
  // No taxonomy here any more. What a clip is about is worked out server-side
  // from the category the page publishes for itself, and set per-site in
  // config.yaml — so there is nothing for the browser to store or to ask for.
};

export async function loadSettings() {
  const { settings } = await chrome.storage.local.get('settings');
  return { ...DEFAULTS, ...(settings || {}) };
}

export async function saveSettings(settings) {
  await chrome.storage.local.set({ settings });
}

/** Strip a trailing slash so we never build a URL with a double slash. */
function base(url) {
  return String(url || '').trim().replace(/\/+$/, '');
}

/** Is the server up, and can it write to the vault? */
export async function checkHealth(settings, timeoutMs = 2500) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const resp = await fetch(base(settings.serverUrl) + '/health', {
      signal: controller.signal
    });
    if (!resp.ok) {
      return { up: false, reason: `Server answered ${resp.status}` };
    }
    const data = await resp.json();
    return {
      up: true,
      vault: data.vault,
      writable: data.vault_writable,
      authRequired: data.auth_required,
      rules: data.site_rules,
      configError: data.config_error
    };
  } catch (e) {
    return {
      up: false,
      reason: e.name === 'AbortError' ? 'No response' : 'Not reachable'
    };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * POST JSON to the clip server. Throws with a readable message on failure;
 * a network-level failure carries `.unreachable` so callers can fall back.
 */
async function postJson(settings, path, payload) {
  const headers = { 'Content-Type': 'application/json', 'X-Clip-Client': 'extension' };
  if (settings.token) headers['X-Clip-Token'] = settings.token;

  let resp;
  try {
    resp = await fetch(base(settings.serverUrl) + path, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload)
    });
  } catch {
    const err = new Error('Clip server is not reachable.');
    err.unreachable = true;
    throw err;
  }

  if (resp.status === 401) {
    throw new Error('Server rejected the token. Check it in Settings.');
  }
  if (!resp.ok) {
    let detail = `Server returned ${resp.status}`;
    try {
      const body = await resp.json();
      if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* keep the status-code message */ }
    throw new Error(detail);
  }

  return resp.json();
}

export async function getCategories(settings) {
  const headers = {};
  if (settings.token) headers['X-Clip-Token'] = settings.token;
  const response = await fetch(base(settings.serverUrl) + '/categories', { headers });
  if (response.status === 401) {
    throw new Error('Copy an extension token from the Clipstack web app into Settings.');
  }
  if (!response.ok) throw new Error(`Could not load categories (${response.status}).`);
  return response.json();
}

/** Extract and write. */
export async function sendClip(settings, payload) {
  return postJson(settings, '/clip', payload);
}

/** Extract without writing, so the popup can show the note first. */
export async function previewClip(settings, payload) {
  return postJson(settings, '/preview', payload);
}

/**
 * Last resort when the container is down. There is no extractor in the browser
 * any more, so save the raw HTML rather than lose the clip. It can be
 * re-processed later.
 */
export function fallbackFile(payload) {
  return {
    contents: payload.html,
    extension: '.html',
    type: 'text/html;charset=utf-8'
  };
}

export function safeFilename(name) {
  const out = String(name || '')
    .replace(/[\\/:*?"<>|]/g, '-')
    .replace(/[\u0000-\u001f\u007f]/g, '')
    .replace(/\s+/g, ' ')
    .replace(/^[.\s]+|[.\s]+$/g, '')
    .slice(0, 180)
    .trim();
  return out || 'clipped-note';
}
