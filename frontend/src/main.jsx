import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, NavLink, Outlet, Route, Routes, useNavigate } from 'react-router-dom';
import { api } from './api';
import Articles from './pages/Articles';
import ArticleDetail from './pages/ArticleDetail';
import Categories from './pages/Categories';
import './styles.css';

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID;

function Login({ onLogin }) {
  const button = useRef(null);
  const [message, setMessage] = useState('');

  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) { setMessage('Google sign-in is not configured.'); return; }
    function render() {
      window.google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: async ({ credential }) => {
          try { onLogin(await api('/auth/google', { method: 'POST', body: { credential } })); }
          catch (error) { setMessage(error.message); }
        },
      });
      window.google.accounts.id.renderButton(button.current, { theme: 'outline', size: 'large', width: 300 });
    }
    if (window.google) { render(); return; }
    const script = document.createElement('script');
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true; script.onload = render;
    document.head.append(script);
    return () => { script.onload = null; };
  }, [onLogin]);

  return <main className="auth-layout"><section className="auth-card"><div className="brand-mark">C</div><span className="eyebrow">Clipstack</span><h1>Welcome back</h1><p>Sign in to manage your article vault.</p><div ref={button} className="google-button" />{message && <p className="message">{message}</p>}</section></main>;
}

function Shell({ user, onLogout }) {
  const [health, setHealth] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const check = () => api('/health').then(setHealth).catch(() => setHealth({ ok: false }));
    check(); const timer = setInterval(check, 30000); return () => clearInterval(timer);
  }, []);

  async function copyToken() {
    const { token } = await api('/auth/extension-token', { method: 'POST' });
    await navigator.clipboard.writeText(token);
    setCopied(true); setTimeout(() => setCopied(false), 2000);
  }

  return <div className="app-layout">
    <aside className="sidebar"><NavLink className="sidebar-brand" to="/articles"><span>C</span><b>CLIPSTACK</b></NavLink><nav><NavLink to="/articles">Articles</NavLink><NavLink to="/categories">Categories</NavLink></nav></aside>
    <div className="app-main"><header className="header"><span className={`health ${health?.ok ? 'online' : ''}`}><i />{health?.ok ? 'Server online' : 'Server unavailable'}</span><div className="account">{user.picture && <img src={user.picture} alt="" />}<span><b>{user.name || user.email}</b><small>{user.email}</small></span><button className="secondary" onClick={copyToken}>{copied ? 'Copied' : 'Extension token'}</button><button className="secondary" onClick={onLogout}>Logout</button></div></header><main className="app-content"><Outlet /></main></div>
  </div>;
}

function App() {
  const navigate = useNavigate();
  const [user, setUser] = useState(undefined);
  useEffect(() => { api('/auth/me').then(setUser).catch(() => setUser(null)); }, []);

  async function logout() {
    await api('/auth/logout', { method: 'POST' });
    setUser(null); navigate('/login');
  }

  if (user === undefined) return <div className="loading">Loading Clipstack…</div>;
  return <Routes>
    <Route path="/login" element={user ? <Navigate to="/articles" replace /> : <Login onLogin={setUser} />} />
    <Route element={user ? <Shell user={user} onLogout={logout} /> : <Navigate to="/login" replace />}>
      <Route path="/articles" element={<Articles />} />
      <Route path="/articles/:id" element={<ArticleDetail />} />
      <Route path="/categories" element={<Categories />} />
    </Route>
    <Route path="*" element={<Navigate to={user ? '/articles' : '/login'} replace />} />
  </Routes>;
}

createRoot(document.getElementById('root')).render(<StrictMode><BrowserRouter><App /></BrowserRouter></StrictMode>);
