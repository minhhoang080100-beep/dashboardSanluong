import { BarChart3, Database, LayoutDashboard, Ship, Users } from 'lucide-react';
import { useEffect, useState } from 'react';
import Dashboard from './components/Dashboard';
import portLogo from './assets/nghetinh-port-logo.png';
import './App.css';

const sections = [
  { href: '#overview', label: 'Tổng quan', icon: LayoutDashboard },
  { href: '#production', label: 'Phân tích sản lượng', icon: BarChart3 },
  { href: '#voyages', label: 'Chuyến tàu', icon: Ship },
  { href: '#customers', label: 'Khách hàng', icon: Users },
  { href: '#data-quality', label: 'Nguồn & định nghĩa', icon: Database },
];

function App() {
  const [activeSection, setActiveSection] = useState(() => window.location.hash || '#overview');
  useEffect(() => {
    const handleHash = () => setActiveSection(window.location.hash || '#overview');
    window.addEventListener('hashchange', handleHash);
    return () => window.removeEventListener('hashchange', handleHash);
  }, []);
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Đến nội dung chính</a>
      <aside className="sidebar" aria-label="Điều hướng dashboard">
        <a className="brand" href="#overview" aria-label="Cảng Nghệ Tĩnh — Tổng quan">
          <img className="brand-logo" src={portLogo} width="918" height="577" alt="NgheTinhPort – Cảng Nghệ Tĩnh" />
        </a>
        <p className="nav-label">ĐIỀU HÀNH SẢN XUẤT</p>
        <nav className="nav-links" aria-label="Các mục báo cáo">
          {sections.map(({ href, label, icon: Icon }) => (
            <a className="nav-item" href={href} key={href} aria-current={activeSection === href ? 'location' : undefined}><Icon size={18} aria-hidden="true" /><span>{label}</span></a>
          ))}
        </nav>
      </aside>
      <main className="main-content" id="main-content" tabIndex={-1}>
        <header className="top-header">
          <span><span className="breadcrumb-root">Điều hành</span><span aria-hidden="true"> / </span>Sản xuất & khai thác</span>
          <span className="header-tag">Góc nhìn ban lãnh đạo</span>
        </header>
        <Dashboard />
      </main>
    </div>
  );
}

export default App;
