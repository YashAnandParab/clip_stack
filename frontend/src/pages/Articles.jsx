import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';

const PAGE_SIZE = 10;

export default function Articles() {
  const navigate = useNavigate();
  const [articles, setArticles] = useState([]);
  const [categories, setCategories] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [url, setUrl] = useState('');
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  async function load() {
    try {
      const [history, taxonomy] = await Promise.all([
        api(`/articles?limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`),
        api('/categories'),
      ]);
      setArticles(history.items);
      setTotal(history.total);
      setCategories(taxonomy);
    } catch (error) {
      setMessage(error.message);
    }
  }

  useEffect(() => { load(); }, [page]);

  async function extract(event) {
    event.preventDefault();
    setBusy(true); setMessage(''); setPreview(null);
    try {
      const parsed = new URL(url);
      if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error();
    } catch {
      setBusy(false); setMessage('Enter a complete http(s) article URL.'); return;
    }
    try {
      setPreview(await api('/preview-url', { method: 'POST', body: { url } }));
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  }

  function changeCategory(category) {
    setPreview({ ...preview, category, subcategory: '' });
  }

  async function save() {
    if (!preview?.category || !preview?.subcategory) return;
    setBusy(true); setMessage('');
    try {
      const result = await api('/clip', {
        method: 'POST',
        body: {
          html: preview.html,
          url: preview.url,
          category: preview.category,
          subcategory: preview.subcategory,
          ocr_images: Boolean(preview.ocr_images),
        },
      });
      navigate(`/articles/${result.article_id}`);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  }

  const selected = categories.find((item) => item.name === preview?.category);
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return <>
    <div className="page-heading"><div><span>Library</span><h1>Articles</h1><p>Extract, review, and save useful reading as Markdown.</p></div><button className="secondary" onClick={load}>Refresh</button></div>
    <section className="panel scrape-panel">
      <form className="url-form" onSubmit={extract}>
        <input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com/article" aria-label="Article URL" />
        <button disabled={busy || !url.trim()}>{busy ? 'Extracting…' : 'Extract article'}</button>
      </form>
      {message && <p className="message" role="status">{message}</p>}
      {preview && <div className="preview-card">
        <div><span className="eyebrow">Preview</span><h2>{preview.title || 'Untitled article'}</h2><p>{preview.site || preview.url} · {preview.word_count.toLocaleString()} words</p></div>
        <div className="filing-grid">
          <label>Category<select value={preview.category} onChange={(event) => changeCategory(event.target.value)}><option value="">Choose category</option>{categories.map((item) => <option key={item.id}>{item.name}</option>)}</select></label>
          <label>Subcategory<select value={preview.subcategory} disabled={!selected} onChange={(event) => setPreview({ ...preview, subcategory: event.target.value })}><option value="">Choose subcategory</option>{selected?.subcategories.map((name) => <option key={name}>{name}</option>)}</select></label>
        </div>
        <label className="check"><input type="checkbox" checked={Boolean(preview.ocr_images)} onChange={(event) => setPreview({ ...preview, ocr_images: event.target.checked })} /> Process meaningful article images with OCR</label>
        <button onClick={save} disabled={busy || !preview.category || !preview.subcategory}>{busy ? 'Saving…' : 'Save to MongoDB and vault'}</button>
      </div>}
    </section>
    <section className="panel">
      <div className="section-title"><h2>Saved articles</h2><span>{total}</span></div>
      {articles.length ? <div className="article-list">{articles.map((article) => <Link className="article-row" key={article.id} to={`/articles/${article.id}`}><div><small>{article.site || 'Unknown source'}</small><strong>{article.title}</strong><span>{article.category} / {article.subcategory}</span></div><time>{new Date(article.clipped_at).toLocaleDateString()}</time></Link>)}</div> : <div className="empty">No saved articles yet.</div>}
      {pages > 1 && <nav className="pagination" aria-label="Article pages"><button className="secondary" disabled={!page} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page + 1} of {pages}</span><button className="secondary" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</button></nav>}
    </section>
  </>;
}
