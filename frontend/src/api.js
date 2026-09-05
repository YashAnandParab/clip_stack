export const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8765';

export async function api(path, options = {}) {
  const request = { credentials: 'include', ...options, headers: { ...options.headers } };
  if (request.body && typeof request.body !== 'string') {
    request.headers['Content-Type'] = 'application/json';
    request.body = JSON.stringify(request.body);
  }
  const response = await fetch(`${API}${path}`, request);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || `Server returned ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}
