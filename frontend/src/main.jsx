import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8765';
const TAXONOMY = {
  'Investor Finance': ['Behavioral Finance', 'Personal Finance', 'Nano Learning', 'Investing', 'Markets', 'Money'],
  Work: ['AI', 'Database'], News: ['Political', 'Sports'], Entertainment: ['Movies', 'Songs']
};

const Icon = ({ name }) => name === 'arrow' ? <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 10h13m-5-5 5 5-5 5" /></svg> : <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 5h12M4 10h8M4 15h12" /></svg>;

function Select({ label, value, onChange, children }) {
  return <label className="select-field"><span>{label}</span><select value={value} onChange={onChange}><option value="">Select {label.toLowerCase()}</option>{children}</select></label>;
}

function MetaTable({ article, onChange }) {
  const rows = [['Source', article.site || 'Unknown'], ['Author', article.author || 'Not detected'], ['Published', article.published || 'Not detected'], ['Words', article.word_count?.toLocaleString() || '—'], ['Strategy', article.strategy || '—'], ['URL', article.url]];
  return <div className="detail-grid">
    <div className="table-wrap"><table><tbody>{rows.map(([key, value]) => <tr key={key}><th>{key}</th><td className={key === 'URL' ? 'url-cell' : ''}>{value}</td></tr>)}</tbody></table></div>
    <div className="taxonomy">
      <div className="taxonomy-head"><span>Filing</span>{article.category && article.subcategory ? <em className="detected">Auto-detected</em> : <em className="needs">Needs your choice</em>}</div>
      <Select label="Category" value={article.category} onChange={(e) => onChange({ category: e.target.value, subcategory: '' })}>{Object.keys(TAXONOMY).map((item) => <option key={item} value={item}>{item}</option>)}</Select>
      <Select label="Subcategory" value={article.subcategory} onChange={(e) => onChange({ subcategory: e.target.value })}>{(TAXONOMY[article.category] || []).map((item) => <option key={item} value={item}>{item}</option>)}</Select>
    </div>
  </div>;
}

function ArticleCard({ article, open, onToggle, onChange, onSave }) {
  const ready = article.category && article.subcategory;
  return <article className={`article-card ${open ? 'is-open' : ''}`}>
    <button className="card-trigger" onClick={onToggle} aria-expanded={open}>
      <span className="status-mark" data-state={article.status}><span /></span>
      <span className="card-copy"><span className="card-site">{article.site || new URL(article.url).hostname.replace('www.', '')}</span><strong>{article.title || 'Untitled article'}</strong><span className="card-meta">{article.published || 'Date not detected'} <i /> {article.word_count?.toLocaleString() || '—'} words</span></span>
      <span className="chevron">{open ? '−' : '+'}</span>
    </button>
    {open && <div className="card-detail"><MetaTable article={article} onChange={onChange} /><div className="detail-footer"><span>{ready ? `${article.category} / ${article.subcategory}` : 'Choose both filing fields to continue'}</span><button className="save-button" disabled={!ready || article.saving} onClick={onSave}>{article.saving ? 'Sending…' : 'Send to vault'} <Icon name="arrow" /></button></div></div>}
  </article>;
}

function App() {
  const [url, setUrl] = useState('');
  const [articles, setArticles] = useState([]);
  const [openId, setOpenId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [message, setMessage] = useState('');
  const previewTimer = useRef(null);

  async function request(path, body) {
    const response = await fetch(`${API}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Server returned ${response.status}`);
    return data;
  }

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
    try { const result = await request('/clip', { html: article.html, url: article.url, category: article.category, subcategory: article.subcategory }); update(article.id, { saving: false, status: 'saved', path: result.path }); setMessage(`Saved to ${result.path}`); }
    catch (error) { update(article.id, { saving: false }); setMessage(error.message); }
  }

  return <div className="app-shell">
    <header className="topbar"><a className="brand" href="/"><span className="brand-dot" />Clip to Vault</a><span className="server-state"><span />Local server <b>online</b></span></header>
    <main>
      <section className="intro"><p className="section-label">Article workbench</p><h1>Keep the good<br /><span>bits of the web.</span></h1><p className="lede">Paste a URL. We’ll extract the article, read its metadata, and put a clean copy in your vault.</p></section>
      <form className="url-form" onSubmit={scrape}><div className="input-leading"><Icon name="menu" /><input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/article" aria-label="Article URL" /></div><button type="submit" disabled={busy || previewing || !url.trim()}>{previewing ? 'Reading metadata…' : articles.some((item) => item.url === url.trim() && item.status === 'ready') ? 'Save to vault' : articles.some((item) => item.url === url.trim() && item.status === 'needs') ? 'Choose filing' : 'Scrape & save'} <Icon name="arrow" /></button></form>
      {message && <p className="message" role="status">{message}</p>}
      <section className="library"><div className="library-head"><div><h2>Clipped articles</h2><p>{articles.length ? `${articles.length} article${articles.length === 1 ? '' : 's'} in this session` : 'Your reviewed articles will appear here'}</p></div><span className="count">{String(articles.length).padStart(2, '0')}</span></div>
        {articles.length ? <div className="cards">{articles.map((article) => <ArticleCard key={article.id} article={article} open={openId === article.id} onToggle={() => setOpenId(openId === article.id ? null : article.id)} onChange={(changes) => update(article.id, changes)} onSave={() => save(article)} />)}</div> : <div className="empty"><span className="empty-line" /><p>Nothing clipped yet.</p><span>Start with an article URL above.</span></div>}
      </section>
    </main>
    <footer><span>CLIPSTACK / LOCAL WORKSPACE</span><span>Metadata first. Guessing never.</span></footer>
  </div>;
}

createRoot(document.getElementById('root')).render(<StrictMode><App /></StrictMode>);
