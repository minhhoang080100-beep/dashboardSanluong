import { BarChart3, ClipboardList, Settings } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import Dashboard from './components/Dashboard';
import Auth from './components/Auth';
import portLogo from './assets/nghetinh-port-logo.png';
import './App.css';

const sections = [
  { href: '#overview', view: 'reports', label: 'Báo cáo sản lượng', icon: BarChart3 },
  { href: '#management', view: 'management', label: 'Kế hoạch', icon: ClipboardList },
  { href: '#admin', view: 'admin', label: 'Quản trị', icon: Settings },
];

function permittedHash(hash, user) {
  if (hash === '#admin') return user.role === 'admin' ? hash : '#overview';
  return ['#overview', '#production', '#voyages', '#customers', '#data-quality', '#management'].includes(hash) ? hash : '#overview';
}

function AppShell({ user, logout, changePassword }) {
  const shell = useRef(null);
  const header = useRef(null);
  const main = useRef(null);
  const [hash, setHash] = useState(() => permittedHash(window.location.hash, user));
  const activeView = hash === '#management' ? 'management' : hash === '#admin' ? 'admin' : 'reports';
  useEffect(() => {
    const element = header.current;
    if (!element) return;
    const measure = () => shell.current?.style.setProperty('--header-height', `${element.getBoundingClientRect().height}px`);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const handleHash = () => {
      const next = permittedHash(window.location.hash, user);
      if (window.location.hash && window.location.hash !== next) window.history.replaceState(null, '', next);
      setHash(next);
    };
    handleHash();
    window.addEventListener('hashchange', handleHash);
    return () => window.removeEventListener('hashchange', handleHash);
  }, [user]);
  useEffect(() => {
    if (!['#overview', '#management', '#admin'].includes(hash)) return;
    const frame = requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    return () => cancelAnimationFrame(frame);
  }, [hash]);

  return (
    <div ref={shell} className="app-shell">
      <a className="skip-link" href="#main-content" onClick={(event) => { event.preventDefault(); main.current?.focus(); }}>Đến nội dung chính</a>
      <header ref={header} className="top-header">
        <div className="header-content">
          <a className="brand" href="#overview" aria-label="Cảng Nghệ Tĩnh — Báo cáo sản lượng">
            <img className="brand-logo" src={portLogo} width="918" height="577" alt="NgheTinhPort – Cảng Nghệ Tĩnh" />
          </a>
          <nav className="top-nav" aria-label="Điều hướng chính">
            {sections.filter((section) => section.view !== 'admin' || user.role === 'admin').map(({ href, view, label, icon: Icon }) => (
              <a className="nav-item" href={href} key={href} aria-current={activeView === view ? 'page' : undefined} onClick={() => { if (window.location.hash === href) window.scrollTo({ top: 0, behavior: 'instant' }); }}><Icon size={17} aria-hidden="true" /><span>{label}</span></a>
            ))}
          </nav>
          <div className="user-tools"><span title={user.display_name}>{user.display_name}</span><button className="button" type="button" onClick={changePassword}>Đổi mật khẩu</button><button className="button" type="button" onClick={logout}>Đăng xuất</button></div>
        </div>
      </header>
      <main ref={main} className="main-content" id="main-content" tabIndex={-1}>
        <Dashboard user={user} activeView={activeView} anchor={hash} />
      </main>
    </div>
  );
}

function App() {
  return <Auth>{({ user, logout, changePassword }) => <AppShell key={user.id} user={user} logout={logout} changePassword={changePassword} />}</Auth>;
}

export default App;
