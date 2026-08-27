import React, { useState, useEffect } from 'react';
import { 
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, 
  AreaChart, Area, PieChart, Pie, Cell, Legend
} from 'recharts';
import { TrendingUp, Activity, Package, Clock, MapPin, Users, Navigation, Zap, Database } from 'lucide-react';
import './Dashboard.css';

const COLORS = ['#3b82f6', '#10b981', '#8b5cf6', '#f59e0b', '#ef4444'];
const TERMINAL_COLORS = ['#3b82f6', '#f59e0b'];
const DIR_COLORS = ['#10b981', '#8b5cf6', '#3b82f6', '#94a3b8'];

function Dashboard() {
  const [overview, setOverview] = useState(null);
  const [cargoData, setCargoData] = useState([]);
  const [historyData, setHistoryData] = useState([]);
  const [terminalData, setTerminalData] = useState([]);
  const [directionData, setDirectionData] = useState([]);
  const [customerData, setCustomerData] = useState([]);
  const [efficiency, setEfficiency] = useState({ turnaround_time: 0, productivity: 0 });
  const [yardData, setYardData] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        const API_BASE = 'http://localhost:8000/api';
        
        const [overviewRes, cargoRes, historyRes, terminalRes, directionRes, customerRes, efficiencyRes, yardRes] = await Promise.all([
          fetch(`${API_BASE}/overview`),
          fetch(`${API_BASE}/cargo-breakdown`),
          fetch(`${API_BASE}/throughput-history`),
          fetch(`${API_BASE}/terminal-breakdown`),
          fetch(`${API_BASE}/direction-breakdown`),
          fetch(`${API_BASE}/top-customers`),
          fetch(`${API_BASE}/efficiency`),
          fetch(`${API_BASE}/yard-occupancy`)
        ]);

        if (overviewRes.ok) setOverview(await overviewRes.json());
        if (cargoRes.ok) setCargoData(await cargoRes.json());
        if (historyRes.ok) setHistoryData(await historyRes.json());
        if (terminalRes.ok) setTerminalData(await terminalRes.json());
        if (directionRes.ok) setDirectionData(await directionRes.json());
        if (customerRes.ok) setCustomerData(await customerRes.json());
        if (efficiencyRes.ok) setEfficiency(await efficiencyRes.json());
        if (yardRes.ok) setYardData(await yardRes.json());

      } catch (error) {
        console.error("Error fetching live data", error);
        // Fallback or error state could be handled here
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  if (loading) {
    return (
      <div className="loading-state">
        <div className="spinner"></div>
        <p>Đang tổng hợp dữ liệu từ SmartTOS & SmartTOS_BenThuy...</p>
      </div>
    );
  }

  return (
    <div className="dashboard animate-fade-in">
      <div className="dashboard-header">
        <div>
          <h2>Phân tích Sản lượng Toàn Cảng</h2>
          <p className="text-muted">Cập nhật lúc: {new Date().toLocaleString('vi-VN')} | Đã hợp nhất dữ liệu Cửa Lò & Bến Thủy</p>
        </div>
        <button className="btn-primary">
          <Activity size={16} /> Báo cáo chi tiết
        </button>
      </div>

      <div className="kpi-grid">
        <div className="kpi-card card">
          <div className="kpi-icon blue">
            <Package size={24} />
          </div>
          <div className="kpi-content">
            <p className="kpi-title">Sản lượng Container (TEU)</p>
            <h3 className="kpi-value">{overview.total_teu.toLocaleString()}</h3>
            <p className={`kpi-trend ${overview.trend_teu >= 0 ? 'positive' : 'negative'}`}>
              <TrendingUp size={14}/> {overview.trend_teu >= 0 ? '+' : ''}{overview.trend_teu}% so với cùng kỳ T.trước
            </p>
          </div>
        </div>

        <div className="kpi-card card">
          <div className="kpi-icon green">
            <Activity size={24} />
          </div>
          <div className="kpi-content">
            <p className="kpi-title">Sản lượng Hàng Rời/TH (Tấn)</p>
            <h3 className="kpi-value">{overview.total_tonnage.toLocaleString()}</h3>
            <p className={`kpi-trend ${overview.trend_tonnage >= 0 ? 'positive' : 'negative'}`}>
              <TrendingUp size={14}/> {overview.trend_tonnage >= 0 ? '+' : ''}{overview.trend_tonnage}% so với cùng kỳ T.trước
            </p>
          </div>
        </div>

        <div className="kpi-card card">
          <div className="kpi-icon purple">
            <Activity size={24} />
          </div>
          <div className="kpi-content">
            <p className="kpi-title">Lượt tàu thông qua</p>
            <h3 className="kpi-value">{overview.vessel_calls}</h3>
            <p className={`kpi-trend ${overview.trend_vessels >= 0 ? 'positive' : 'negative'}`}>
              <TrendingUp size={14}/> {overview.trend_vessels >= 0 ? '+' : ''}{overview.trend_vessels} tàu so với cùng kỳ T.trước
            </p>
          </div>
        </div>

        <div className="kpi-card card">
          <div className="kpi-icon orange">
            <Clock size={24} />
          </div>
          <div className="kpi-content">
            <p className="kpi-title">Thời gian quay vòng tàu</p>
            <h3 className="kpi-value">{efficiency.turnaround_time} <span style={{fontSize:'1rem', color:'var(--text-muted)'}}>Giờ</span></h3>
            <p className="kpi-trend neutral">TB mỗi chuyến tàu cập bến</p>
          </div>
        </div>

        <div className="kpi-card card">
          <div className="kpi-icon red">
            <Zap size={24} />
          </div>
          <div className="kpi-content">
            <p className="kpi-title">Năng suất xếp dỡ (Tấn/Máng-Giờ)</p>
            <h3 className="kpi-value">{efficiency.productivity}</h3>
            <p className="kpi-trend neutral">TB toàn bộ máng xếp dỡ</p>
          </div>
        </div>
      </div>

      {/* Tầng 1: Lịch sử và Xí nghiệp */}
      <div className="charts-grid-half">
        <div className="chart-card card">
          <h3 className="chart-title">Xu hướng Sản lượng 6 tháng</h3>
          <div className="chart-container">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={historyData} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorTeu" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.8}/>
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <XAxis dataKey="date" stroke="#94a3b8" />
                <YAxis stroke="#94a3b8" />
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
                <RechartsTooltip contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', borderRadius: '8px' }} itemStyle={{ color: '#f8fafc' }} />
                <Area type="monotone" dataKey="teu" name="Container (TEU)" stroke="#3b82f6" fillOpacity={1} fill="url(#colorTeu)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="chart-card card">
          <h3 className="chart-title"><MapPin size={18} style={{display:'inline', marginRight: '8px', verticalAlign: 'middle'}}/> So sánh Xí nghiệp (Tấn)</h3>
          <div className="chart-container">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={terminalData} margin={{ top: 20, right: 30, left: 20, bottom: 5 }} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" horizontal={false} />
                <XAxis type="number" stroke="#94a3b8" />
                <YAxis dataKey="name" type="category" stroke="#94a3b8" width={80} />
                <RechartsTooltip cursor={{fill: 'rgba(255,255,255,0.05)'}} contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', borderRadius: '8px' }} />
                <Bar dataKey="tonnage" name="Sản lượng Hàng (Tấn)" radius={[0, 4, 4, 0]}>
                  {terminalData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={TERMINAL_COLORS[index % TERMINAL_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Tầng 2: Cơ cấu hàng hóa và Hướng hàng */}
      <div className="charts-grid-third">
        <div className="chart-card card">
          <h3 className="chart-title"><Package size={18} style={{display:'inline', marginRight: '8px', verticalAlign: 'middle'}}/> Chi tiết Nhóm Hàng</h3>
          <div className="chart-container" style={{height: '250px'}}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={cargoData} cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={2} dataKey="value">
                  {cargoData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Pie>
                <RechartsTooltip contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', borderRadius: '8px' }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="pie-legend">
            {cargoData.map((entry, index) => (
              <div key={index} className="legend-item" style={{justifyContent: 'space-between'}}>
                <div style={{display: 'flex', alignItems: 'center', gap: '0.75rem'}}>
                  <span className="legend-color" style={{ backgroundColor: COLORS[index % COLORS.length] }}></span>
                  <span>{entry.name}</span>
                </div>
                <strong>{entry.value}%</strong>
              </div>
            ))}
          </div>
        </div>

        <div className="chart-card card">
          <h3 className="chart-title"><Navigation size={18} style={{display:'inline', marginRight: '8px', verticalAlign: 'middle'}}/> Cơ cấu Hướng Hàng</h3>
          <div className="chart-container" style={{height: '250px'}}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={directionData} cx="50%" cy="50%" outerRadius={80} dataKey="value">
                  {directionData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={DIR_COLORS[index % DIR_COLORS.length]} />
                  ))}
                </Pie>
                <RechartsTooltip contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', borderRadius: '8px' }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="pie-legend">
            {directionData.map((entry, index) => (
              <div key={index} className="legend-item" style={{justifyContent: 'space-between'}}>
                <div style={{display: 'flex', alignItems: 'center', gap: '0.75rem'}}>
                  <span className="legend-color" style={{ backgroundColor: DIR_COLORS[index % DIR_COLORS.length] }}></span>
                  <span>{entry.name}</span>
                </div>
                <strong>{entry.value}%</strong>
              </div>
            ))}
          </div>
        </div>

        <div className="chart-card card">
          <h3 className="chart-title"><Users size={18} style={{display:'inline', marginRight: '8px', verticalAlign: 'middle'}}/> Top 5 Chủ Hàng</h3>
          <div className="chart-container" style={{height: '250px'}}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={customerData} layout="vertical" margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" horizontal={false} />
                <XAxis type="number" hide />
                <YAxis dataKey="name" type="category" stroke="#94a3b8" width={100} tick={{fontSize: 11}} />
                <RechartsTooltip cursor={{fill: 'rgba(255,255,255,0.05)'}} contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', borderRadius: '8px' }} />
                <Bar dataKey="volume" name="Sản lượng (Tấn)" fill="#8b5cf6" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Tầng 3: Tỷ lệ lấp đầy Kho bãi */}
      <div className="charts-grid-full mt-4" style={{marginTop: '1.5rem'}}>
        <div className="chart-card card">
          <h3 className="chart-title"><Database size={18} style={{display:'inline', marginRight: '8px', verticalAlign: 'middle'}}/> Tỷ lệ lấp đầy Kho/Bãi (%)</h3>
          <div className="chart-container" style={{height: '300px'}}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={yardData} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
                <XAxis dataKey="name" stroke="#94a3b8" />
                <YAxis stroke="#94a3b8" domain={[0, 100]} />
                <RechartsTooltip cursor={{fill: 'rgba(255,255,255,0.05)'}} contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', borderRadius: '8px' }} />
                <Bar dataKey="occupancy" name="Tỷ lệ lấp đầy (%)" radius={[4, 4, 0, 0]}>
                  {yardData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.occupancy > 80 ? '#ef4444' : entry.occupancy > 50 ? '#f59e0b' : '#10b981'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
}

export default Dashboard;
