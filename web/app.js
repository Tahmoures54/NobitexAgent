/* ============================================================
   CryptoScanner Web — Shared utilities
   External identity auth: Google + Microsoft
   ============================================================ */
(function () {
  'use strict';

  const TOKEN_KEY = 'cs_token';
  const USER_KEY = 'cs_user';

  const Auth = {
    getToken() { return localStorage.getItem(TOKEN_KEY); },
    getUser() {
      try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); }
      catch { return null; }
    },
    isLoggedIn() { return !!this.getToken(); },
    setSession(token, user) {
      localStorage.setItem(TOKEN_KEY, token);
      localStorage.setItem(USER_KEY, JSON.stringify(user || {}));
    },
    clear() {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
    },
    logout() {
      this.clear();
      window.location.href = '/login.html';
    },
    requireLogin() {
      if (!this.isLoggedIn()) {
        const next = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.href = `/login.html?next=${next}`;
        return false;
      }
      return true;
    },
  };

  async function request(method, path, body) {
    const headers = { 'Accept': 'application/json' };
    const token = Auth.getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    let payload;
    if (body !== undefined && body !== null) {
      headers['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }

    let resp;
    try {
      resp = await fetch(path, { method, headers, body: payload });
    } catch {
      throw new Error('Network error — check your connection.');
    }

    if (resp.status === 401) Auth.clear();

    let data = null;
    const ct = resp.headers.get('content-type') || '';
    if (ct.includes('application/json')) {
      try { data = await resp.json(); } catch { data = null; }
    } else {
      data = await resp.text();
    }

    if (!resp.ok) {
      const msg = (data && data.detail) || `HTTP ${resp.status}`;
      const err = new Error(Array.isArray(msg) ? msg[0]?.msg || JSON.stringify(msg) : msg);
      err.status = resp.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  const API = {
    get: (path) => request('GET', path),
    post: (path, body) => request('POST', path, body),
    put: (path, body) => request('PUT', path, body),
    patch: (path, body) => request('PATCH', path, body),
    delete: (path, body) => request('DELETE', path, body),

    auth: {
      register: (email, fullName, phoneNumber) =>
        request('POST', '/auth/register', {
          email, full_name: fullName, phone_number: phoneNumber,
        }),
      setupVerify: (email, code) =>
        request('POST', '/auth/setup/verify', { email, code }),
      login: (email, code) =>
        request('POST', '/auth/login', { email, code }),
      me: () => request('GET', '/auth/me'),
      logout: () => request('POST', '/auth/logout'),
    },
    scan: {
      cached: () => request('GET', '/api/scan'),
      refresh: () => request('POST', '/api/scan/refresh'),
      status: () => request('GET', '/api/scan/status'),
    },

    paper: {
      open: (payload) => request('POST', '/api/paper/open', payload),
      close: (id, price, reason) =>
        request('POST', `/api/paper/${id}/close`, { price, reason }),
      list: () => request('GET', '/api/paper/list'),
      stats: () => request('GET', '/api/paper/stats'),
      remove: (id) => request('DELETE', `/api/paper/${id}`),
    },

    admin: {
      stats: () => request('GET', '/admin/stats'),
    },
  };

  const Fmt = {
    price(v, decimals) {
      const n = Number(v);
      if (v == null || isNaN(n)) return '—';
      const d = decimals != null ? decimals
        : Math.abs(n) >= 1000 ? 2
        : Math.abs(n) >= 1 ? 4
        : Math.abs(n) >= 0.01 ? 6 : 8;
      return n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
    },
    usd(v, decimals = 2) {
      const n = Number(v);
      if (v == null || isNaN(n)) return '—';
      const sign = n < 0 ? '-' : '';
      return `${sign}$${Math.abs(n).toLocaleString('en-US', {
        minimumFractionDigits: decimals, maximumFractionDigits: decimals,
      })}`;
    },
    usdSigned(v, decimals = 2) {
      const n = Number(v);
      if (v == null || isNaN(n)) return '—';
      const sign = n > 0 ? '+' : n < 0 ? '-' : '';
      return `${sign}$${Math.abs(n).toFixed(decimals)}`;
    },
    pct(v, decimals = 2) {
      const n = Number(v);
      if (v == null || isNaN(n)) return '—';
      return `${n > 0 ? '+' : ''}${n.toFixed(decimals)}%`;
    },
    compact(v) {
      const n = Number(v);
      if (v == null || isNaN(n)) return '—';
      const abs = Math.abs(n);
      if (abs >= 1e12) return (n / 1e12).toFixed(2) + 'T';
      if (abs >= 1e9) return (n / 1e9).toFixed(2) + 'B';
      if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M';
      if (abs >= 1e3) return (n / 1e3).toFixed(2) + 'K';
      return n.toFixed(2);
    },
    datetime(iso, opts) {
      if (!iso) return '—';
      try {
        return new Date(iso).toLocaleString('en-US', opts || {
          month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
        });
      } catch { return '—'; }
    },
    relTime(unixSec) {
      if (!unixSec) return '—';
      const s = Math.floor(Date.now() / 1000 - unixSec);
      if (s < 5) return 'just now';
      if (s < 60) return `${s}s ago`;
      if (s < 3600) return `${Math.floor(s / 60)}m ago`;
      if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
      return `${Math.floor(s / 86400)}d ago`;
    },
    colorClass(v) {
      const n = Number(v);
      if (v == null || isNaN(n) || n === 0) return 'text-flat';
      return n > 0 ? 'text-up' : 'text-down';
    },
    signalBadgeClass(signal) {
      switch ((signal || '').toLowerCase()) {
        case 'strong buy': return 'badge badge-strong-buy';
        case 'buy signal': return 'badge badge-buy';
        case 'sell signal': return 'badge badge-sell';
        case 'strong sell': return 'badge badge-strong-sell';
        default: return 'badge badge-neutral';
      }
    },
    riskBadgeClass(level) {
      switch ((level || '').toLowerCase()) {
        case 'low': return 'badge badge-low';
        case 'medium': return 'badge badge-medium';
        case 'high': return 'badge badge-high';
        case 'extreme': return 'badge badge-extreme';
        default: return 'badge badge-neutral';
      }
    },
  };

  const UI = {
    _container: null,
    _ensureContainer() {
      if (this._container) return this._container;
      let c = document.getElementById('toast-container');
      if (!c) {
        c = document.createElement('div');
        c.id = 'toast-container';
        document.body.appendChild(c);
      }
      this._container = c;
      return c;
    },
    toast(message, type = 'info', duration = 3500) {
      const c = this._ensureContainer();
      const el = document.createElement('div');
      el.className = `toast toast-${type}`;
      el.textContent = String(message);
      c.appendChild(el);
      setTimeout(() => {
        el.style.transition = 'opacity .3s, transform .3s';
        el.style.opacity = '0';
        el.style.transform = 'translateX(20px)';
        setTimeout(() => el.remove(), 300);
      }, duration);
    },
    success(msg) { this.toast(msg, 'success'); },
    error(msg) { this.toast(msg, 'error', 5000); },
    warn(msg) { this.toast(msg, 'warning'); },
    confirm(message, detail) {
      return window.confirm(detail ? `${message}\n\n${detail}` : message);
    },
  };

  window.API = API;
  window.Auth = Auth;
  window.Fmt = Fmt;
  window.UI = UI;

  document.addEventListener('alpine:init', () => {
    if (!window.Alpine) return;
    window.Alpine.store('user', {
      get current() { return Auth.getUser(); },
      get loggedIn() { return Auth.isLoggedIn(); },
      get isPremium() {
        const u = Auth.getUser();
        return !!u && (u.plan === 'pro' || u.plan === 'lifetime');
      },
      logout() { Auth.logout(); },
    });
  });
})();
