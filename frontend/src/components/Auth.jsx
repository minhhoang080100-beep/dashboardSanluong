import { useEffect, useState } from 'react';
import { apiRequest, getSessionToken, setSessionToken } from '../api-client';
import portLogo from '../assets/nghetinh-port-logo.png';
import './Auth.css';

export default function Auth({ children }) {
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [passwordMode, setPasswordMode] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    const expired = () => { setUser(null); setError('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.'); };
    window.addEventListener('dashboard-session-expired', expired);
    if (getSessionToken()) {
      apiRequest('/auth/me', { signal: controller.signal }).then(setUser).catch((failure) => {
        if (!controller.signal.aborted) { setSessionToken(''); setError(failure.message); }
      }).finally(() => { if (!controller.signal.aborted) setChecking(false); });
    } else setChecking(false);
    return () => { controller.abort(); window.removeEventListener('dashboard-session-expired', expired); };
  }, []);

  async function login(event) {
    event.preventDefault(); setBusy(true); setError('');
    const form = new FormData(event.currentTarget);
    try {
      const result = await apiRequest('/auth/login', { method: 'POST', body: { username: form.get('username'), password: form.get('password') } });
      setSessionToken(result.token);
      setUser(result.user);
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  async function changePassword(event) {
    event.preventDefault(); setBusy(true); setError('');
    const form = new FormData(event.currentTarget);
    if (form.get('new_password') !== form.get('confirm_password')) { setError('Mật khẩu nhập lại chưa khớp.'); setBusy(false); return; }
    try {
      const result = await apiRequest('/auth/password', { method: 'POST', body: { current_password: form.get('current_password'), new_password: form.get('new_password') } });
      if (result?.token) setSessionToken(result.token);
      setUser(result?.user || await apiRequest('/auth/me'));
      setPasswordMode(false);
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  async function logout() {
    try { await apiRequest('/auth/logout', { method: 'POST' }); }
    catch { /* Always clear this browser's session, including during an outage. */ }
    finally { setSessionToken(''); setUser(null); setError(''); setPasswordMode(false); }
  }

  if (checking) return <div className="auth-screen"><p role="status">Đang kiểm tra đăng nhập…</p></div>;
  if (user && !user.must_change_password && !passwordMode) return children({ user, logout, changePassword: () => { setError(''); setPasswordMode(true); } });
  return <main className="auth-screen"><section className="auth-card">
    <img src={portLogo} alt="Cảng Nghệ Tĩnh" width="918" height="577" />
    <h1>{user ? 'Đổi mật khẩu' : 'Đăng nhập'}</h1>
    <p>{user ? 'Đặt mật khẩu riêng để sử dụng tài khoản.' : 'Báo cáo sản lượng'}</p>
    <form onSubmit={user ? changePassword : login} aria-busy={busy}>
      {!user ? <><label>Tài khoản<input name="username" autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} required maxLength={80} autoFocus /></label><label>Mật khẩu<input name="password" type="password" autoComplete="current-password" required maxLength={256} /></label></> : <>
        <label>Mật khẩu hiện tại<input name="current_password" type="password" autoComplete="current-password" required maxLength={256} autoFocus /></label>
        <div className="auth-field"><label>Mật khẩu mới<input name="new_password" type="password" autoComplete="new-password" aria-describedby="password-requirements" required minLength={12} maxLength={128} /></label><p id="password-requirements" className="auth-field-help">Tối thiểu 12 ký tự.</p></div>
        <label>Nhập lại mật khẩu mới<input name="confirm_password" type="password" autoComplete="new-password" required minLength={12} maxLength={128} /></label>
      </>}
      {error && <p role="alert" className="auth-error">{error}</p>}
      <button type="submit" className="button primary" disabled={busy}>{busy ? 'Đang xử lý…' : user ? 'Lưu mật khẩu' : 'Đăng nhập'}</button>
      {user && !user.must_change_password && <button type="button" className="button" onClick={() => { setPasswordMode(false); setError(''); }}>Quay lại báo cáo</button>}
      {user && <button type="button" className="button" onClick={logout}>Đăng xuất</button>}
    </form>
  </section></main>;
}
