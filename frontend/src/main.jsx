import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
import './persistence.css';

const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8765';
const PAGE_SIZE = 10;
const TAXONOMY = {
  'Investor Finance': ['Behavioral Finance', 'Personal Finance', 'Nano Learning', 'Investing', 'Markets', 'Money'],
  Work: ['AI', 'Database'], News: ['Political', 'Sports'], Entertainment: ['Movies', 'Songs']
};

const Icon = ({ name }) => name === 'arrow' ? <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 10h13m-5-5 5 5-5 5" /></svg> : <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 5h12M4 10h8M4 15h12" /></svg>;

function Select({ label, value, onChange, disabled = false, children }) {
  return <label className="select-field"><span>{label}</span><select value={value} onChange={onChange} disabled={disabled}><option value="">Select {label.toLowerCase()}</option>{children}</select></label>;
}

function MetaTable({ article, onChange, readOnly = false }) {
  const rows = [['Source', article.site || 'Unknown'], ['Author', article.author || 'Not detected'], ['Published', article.published || 'Not detected'], ['Words', article.word_count?.toLocaleString() || '—'], ['Strategy', article.strategy || '—'], ['URL', article.url]];
  return <div className="detail-grid">
    <div className="table-wrap"><table><tbody>{rows.map(([key, value]) => <tr key={key}><th>{key}</th><td className={key === 'URL' ? 'url-cell' : ''}>{value}</td></tr>)}</tbody></table></div>
    <div className="taxonomy">
      <div className="taxonomy-head"><span>Filing</span>{article.category && article.subcategory ? <em className="detected">Auto-detected</em> : <em className="needs">Needs your choice</em>}</div>
      <Select label="Category" value={article.category} disabled={readOnly} onChange={(e) => onChange({ category: e.target.value, subcategory: '' })}>{Object.keys(TAXONOMY).map((item) => <option key={item} value={item}>{item}</option>)}</Select>
      <Select label="Subcategory" value={article.subcategory} disabled={readOnly} onChange={(e) => onChange({ subcategory: e.target.value })}>{(TAXONOMY[article.category] || []).map((item) => <option key={item} value={item}>{item}</option>)}</Select>
    </div>
  </div>;
}

function ArticleCard({ article, open, onToggle, onChange, onSave, onDelete, deleting }) {
  const ready = article.category && article.subcategory;
  const saved = article.status === 'saved';
  return <article className={`article-card ${open ? 'is-open' : ''}`}>
    <button className="card-trigger" onClick={onToggle} aria-expanded={open}>
      <span className="status-mark" data-state={article.status}><span /></span>
      <span className="card-copy"><span className="card-site">{article.site || new URL(article.url).hostname.replace('www.', '')}</span><strong>{article.title || 'Untitled article'}</strong><span className="card-meta">{article.published || 'Date not detected'} <i /> {article.word_count?.toLocaleString() || '—'} words</span></span>
      <span className="chevron">{open ? '−' : '+'}</span>
    </button>
    {open && <div className="card-detail"><MetaTable article={article} onChange={onChange} readOnly={saved} /><div className="detail-footer"><span>{saved ? article.path : ready ? `${article.category} / ${article.subcategory}` : 'Choose both filing fields to continue'}</span>{saved ? <div className="saved-actions"><span className="saved-label">Saved via {article.source_client || 'unknown'}</span><button className="delete-button" disabled={deleting} onClick={onDelete}>{deleting ? 'Deleting…' : 'Delete article'}</button></div> : <button className="save-button" disabled={!ready || article.saving} onClick={onSave}>{article.saving ? 'Sending…' : 'Send to vault'} <Icon name="arrow" /></button>}</div></div>}
  </article>;
}

function App() {
  const [url, setUrl] = useState('');
  const [articles, setArticles] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [deletingId, setDeletingId] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [message, setMessage] = useState('');
  const previewTimer = useRef(null);

  async function request(path, body, method = body === undefined ? 'GET' : 'POST') {
    const options = { method, headers: { 'X-Clip-Client': 'web' } };
    if (body !== undefined) {
      options.method = 'POST';
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch(`${API}${path}`, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Server returned ${response.status}`);
    return data;
  }

  async function loadHistory(showError = false, pageToLoad = page) {
    try {
      const offset = pageToLoad * PAGE_SIZE;
      const data = await request(`/clips?limit=${PAGE_SIZE}&offset=${offset}`);
      const saved = data.items.map((item) => ({ ...item, history_id: item.id, id: `history-${item.id}`, status: 'saved' }));
      setTotal(data.total);
      setArticles((items) => [...items.filter((item) => item.status !== 'saved'), ...saved]);
    } catch (error) {
      if (showError) setMessage(error.message);
    }
  }

  useEffect(() => {
    loadHistory(true, page);
    const timer = setInterval(() => loadHistory(false, page), 5000);
    return () => clearInterval(timer);
  }, [page]);

  async function previewUrl(value) {
    if (previewing) return;
    setPreviewing(true); setMessage('');
    try {
      const data = await request('/preview-url', { url: value });
      const article = { ...data, inputUrl: value, id: crypto.randomUUID(), status: data.category && data.subcategory ? 'ready' : 'needs' };
      setArticles((items) => items.some((item) => item.inputUrl === value && item.status !== 'saved') ? items : [article, ...items]);
      setOpenId(article.id);
    } catch (error) { setMessage(error.message); } finally { setPreviewing(false); }
  }

  useEffect(() => {
    clearTimeout(previewTimer.current);
    const value = url.trim();
    if (!value || !/^https?:\/\/[^\s]+$/i.test(value)) return;
    previewTimer.current = setTimeout(() => previewUrl(value), 650);
    return () => clearTimeout(previewTimer.current);
  }, [url]);

  async function scrape(e) {
    e.preventDefault(); if (busy || previewing || !url.trim()) return;
    try { new URL(url); } catch { setMessage('Paste a complete article URL, including https://'); return; }
    const article = articles.find((item) => (item.inputUrl === url.trim() || item.url === url.trim()) && item.status !== 'saved');
    if (!article) { await previewUrl(url.trim()); return; }
    if (!article.category || !article.subcategory) { setOpenId(article.id); setMessage('Choose a category and subcategory before saving.'); return; }
    await save(article);
  }

  function update(id, changes) { setArticles((items) => items.map((item) => item.id === id ? { ...item, ...changes, status: (changes.category ?? item.category) && (changes.subcategory ?? item.subcategory) ? 'ready' : 'needs' } : item)); }

  async function save(article) {
    update(article.id, { saving: true }); setMessage('');
    try { const result = await request('/clip', { html: article.html, url: article.url, category: article.category, subcategory: article.subcategory }); update(article.id, { saving: false, status: 'saved', path: result.path }); setPage(0); await loadHistory(false, 0); const ocr = result.ocr_attempted ? ` · OCR ${result.ocr_succeeded}/${result.ocr_attempted}` : ''; setMessage(`Saved to ${result.path}${ocr}`); }
    catch (error) { update(article.id, { saving: false }); setMessage(error.message); }
  }

  async function remove(article) {
    const confirmed = window.confirm(`Permanently delete “${article.title}” from PostgreSQL and remove its Markdown file and images from the vault?`);
    if (!confirmed) return;
    setDeletingId(article.history_id); setMessage('');
    try {
      await request(`/clips/${article.history_id}`, undefined, 'DELETE');
      const remaining = Math.max(0, total - 1);
      const lastPage = Math.max(0, Math.ceil(remaining / PAGE_SIZE) - 1);
      setMessage(`Deleted ${article.path}`);
      if (page > lastPage) setPage(lastPage);
      else await loadHistory(false, page);
    } catch (error) { setMessage(error.message); }
    finally { setDeletingId(null); }
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return <div className="app-shell">
    <header className="topbar"><a className="brand" href="/"><span className="brand-dot" />Clip to Vault</a><span className="server-state"><span />Local server <b>online</b></span></header>
    <main>
      <section className="intro"><p className="section-label">Article workbench</p><h1>Keep the good<br /><span>bits of the web.</span></h1><p className="lede">Paste a URL. We’ll extract the article, read its metadata, and put a clean copy in your vault.</p></section>
      <form className="url-form" onSubmit={scrape}><div className="input-leading"><Icon name="menu" /><input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/article" aria-label="Article URL" /></div><button type="submit" disabled={busy || previewing || !url.trim()}>{previewing ? 'Reading metadata…' : articles.some((item) => item.url === url.trim() && item.status === 'ready') ? 'Save to vault' : articles.some((item) => item.url === url.trim() && item.status === 'needs') ? 'Choose filing' : 'Scrape & save'} <Icon name="arrow" /></button></form>
      {message && <p className="message" role="status">{message}</p>}
      <section className="library"><div className="library-head"><div><h2>Clipped articles</h2><p>{total ? `${total} article${total === 1 ? '' : 's'} saved in your vault history` : 'Your reviewed articles will appear here'}</p></div><span className="count">{String(total).padStart(2, '0')}</span></div>
        {articles.length ? <div className="cards">{articles.map((article) => <ArticleCard key={article.id} article={article} open={openId === article.id} onToggle={() => setOpenId(openId === article.id ? null : article.id)} onChange={(changes) => update(article.id, changes)} onSave={() => save(article)} onDelete={() => remove(article)} deleting={deletingId === article.history_id} />)}</div> : <div className="empty"><span className="empty-line" /><p>Nothing clipped yet.</p><span>Start with an article URL above.</span></div>}
        {total > PAGE_SIZE && <nav className="pagination" aria-label="Article pages"><button disabled={page === 0} onClick={() => setPage((value) => value - 1)}>Previous</button><span>Page {page + 1} of {pageCount}</span><button disabled={page + 1 >= pageCount} onClick={() => setPage((value) => value + 1)}>Next</button></nav>}
      </section>
    </main>
    <footer><span>CLIPSTACK / LOCAL WORKSPACE</span><span>Metadata first. Guessing never.</span></footer>
  </div>;
}

createRoot(document.getElementById('root')).render(<StrictMode><App /></StrictMode>);
