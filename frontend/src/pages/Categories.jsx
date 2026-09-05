import { useEffect, useState } from 'react';
import { api } from '../api';

export default function Categories() {
  const [categories, setCategories] = useState([]);
  const [categoryName, setCategoryName] = useState('');
  const [subNames, setSubNames] = useState({});
  const [message, setMessage] = useState('');

  async function load() {
    try { setCategories(await api('/categories')); }
    catch (error) { setMessage(error.message); }
  }
  useEffect(() => { load(); }, []);

  async function run(action) {
    setMessage('');
    try { await action(); await load(); }
    catch (error) { setMessage(error.message); }
  }

  function create(event) {
    event.preventDefault();
    run(() => api('/categories', { method: 'POST', body: { name: categoryName } }));
    setCategoryName('');
  }

  function addSubcategory(event, category) {
    event.preventDefault();
    const name = subNames[category.id] || '';
    if (!name.trim()) return;
    run(() => api(`/categories/${category.id}/subcategories`, { method: 'POST', body: { name } }));
    setSubNames({ ...subNames, [category.id]: '' });
  }

  return <>
    <div className="page-heading"><div><span>Filing</span><h1>Categories</h1><p>Only values listed here can be saved or auto-detected.</p></div></div>
    <section className="panel">
      <form className="inline-form" onSubmit={create}><input value={categoryName} onChange={(event) => setCategoryName(event.target.value)} placeholder="New category" maxLength="80" required /><button>Add category</button></form>
      {message && <p className="message" role="status">{message}</p>}
    </section>
    <div className="category-grid">{categories.map((category) => <section className="panel category-card" key={category.id}>
      <div className="category-head"><h2>{category.name}</h2><button className="danger-link" disabled={category.subcategories.length > 0} title={category.subcategories.length ? 'Delete its subcategories first' : 'Delete category'} onClick={() => run(() => api(`/categories/${category.id}`, { method: 'DELETE' }))}>Delete</button></div>
      {category.subcategories.length ? <ul>{category.subcategories.map((name) => <li key={name}><span>{name}</span><button aria-label={`Delete ${name}`} onClick={() => run(() => api(`/categories/${category.id}/subcategories?name=${encodeURIComponent(name)}`, { method: 'DELETE' }))}>×</button></li>)}</ul> : <p className="muted">No subcategories.</p>}
      <form className="inline-form" onSubmit={(event) => addSubcategory(event, category)}><input value={subNames[category.id] || ''} onChange={(event) => setSubNames({ ...subNames, [category.id]: event.target.value })} placeholder="New subcategory" maxLength="80" required /><button>Add</button></form>
    </section>)}</div>
  </>;
}
