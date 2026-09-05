import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { API, api } from '../api';

export default function ArticleDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [article, setArticle] = useState(null);
  const [message, setMessage] = useState('');

  useEffect(() => {
    api(`/articles/${id}`).then(setArticle).catch((error) => setMessage(error.message));
  }, [id]);

  function download() {
    const blob = new Blob([article.markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = article.filename; link.click();
    URL.revokeObjectURL(url);
  }

  async function remove() {
    if (!window.confirm(`Delete “${article.title}” from MongoDB and the vault?`)) return;
    try {
      await api(`/articles/${id}`, { method: 'DELETE' });
      navigate('/articles');
    } catch (error) {
      setMessage(error.message);
    }
  }

  if (message) return <div className="panel message">{message}</div>;
  if (!article) return <div className="panel">Loading article…</div>;

  const image = ({ src = '', ...props }) => {
    const local = src.startsWith('assets/');
    const filename = decodeURIComponent(src.split('/').pop());
    return <img {...props} src={local ? `${API}/articles/${id}/assets/${encodeURIComponent(filename)}` : src} />;
  };

  return <>
    <div className="page-heading article-heading"><div><Link to="/articles">← Articles</Link><h1>{article.title}</h1><p>{article.site} · {article.category} / {article.subcategory}</p></div><div className="actions"><button className="secondary" onClick={download}>Download .md</button><button className="danger" onClick={remove}>Delete</button></div></div>
    <section className="panel metadata"><span><b>Author</b>{article.author || 'Not detected'}</span><span><b>Published</b>{article.published || 'Not detected'}</span><span><b>Words</b>{article.word_count.toLocaleString()}</span><span><b>Vault path</b>{article.path}</span></section>
    <article className="panel markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ img: image }}>{article.markdown}</ReactMarkdown></article>
  </>;
}
