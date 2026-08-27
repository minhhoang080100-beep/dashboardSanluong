import React, { useState } from 'react';
import { LayoutDashboard, Ship, Box, FileText, Settings, Search, Bell } from 'lucide-react';
import Dashboard from './components/Dashboard';
import './App.css';

function App() {
  const [activeTab, setActiveTab] = useState('dashboard');

  return (
    <div className="app-container">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="brand">
          <Ship className="brand-icon" size={28} />
          <h1 className="text-gradient">Nghe Tinh Port</h1>
        </div>
        
        <nav className="nav-links">
          <div 
            className={`nav-item ${activeTab === 'dashboard' ? 'active' : ''}`}
            onClick={() => setActiveTab('dashboard')}
          >
            <LayoutDashboard size={20} />
            <span>Tổng quan</span>
          </div>
          <div 
            className={`nav-item ${activeTab === 'cargo' ? 'active' : ''}`}
            onClick={() => setActiveTab('cargo')}
          >
            <Box size={20} />
            <span>Sản lượng Hàng</span>
          </div>
          <div 
            className={`nav-item ${activeTab === 'vessels' ? 'active' : ''}`}
            onClick={() => setActiveTab('vessels')}
          >
            <Ship size={20} />
            <span>Lịch trình Tàu</span>
          </div>
          <div 
            className={`nav-item ${activeTab === 'reports' ? 'active' : ''}`}
            onClick={() => setActiveTab('reports')}
          >
            <FileText size={20} />
            <span>Báo cáo</span>
          </div>
          
          <div style={{ flex: 1 }}></div>
          
          <div 
            className={`nav-item ${activeTab === 'settings' ? 'active' : ''}`}
            onClick={() => setActiveTab('settings')}
          >
            <Settings size={20} />
            <span>Cài đặt</span>
          </div>
        </nav>
      </aside>

      {/* Main Content */}
      <main className="main-content">
        <header className="top-header">
          <div className="search-bar">
            <Search size={18} color="var(--text-muted)" />
            <input type="text" placeholder="Tìm kiếm container, tàu..." />
          </div>
          
          <div className="user-profile">
            <Bell size={20} color="var(--text-muted)" style={{ cursor: 'pointer' }} />
            <div className="avatar">A</div>
            <div>
              <div style={{ fontWeight: 500, fontSize: '0.9rem' }}>Admin</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Phòng Khai Thác</div>
            </div>
          </div>
        </header>

        <div style={{ padding: '2rem' }}>
          {activeTab === 'dashboard' && <Dashboard />}
          {activeTab !== 'dashboard' && (
            <div className="card" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '400px' }}>
              <h2 className="text-muted">Tính năng đang được phát triển...</h2>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

export default App;
