import React, { useCallback, useState } from 'react';
import { User, Mail, Lock, Shield, Phone, Building, CheckCircle, AlertTriangle, Monitor } from 'lucide-react';
import { fetchWithAuth } from '@/utils/fetchWithAuth';

const API = process.env.NEXT_PUBLIC_BACKEND_URL || '';

const ROLE_LABELS: Record<string, string> = {
  'System Administrator': 'Platform Administrator',
  'Organization Administrator': 'Organization Administrator',
  'Clinic Administrator': 'Clinic Administrator',
  'Clinic Manager': 'Clinic Administrator',
  'Operations Manager': 'Operations Manager',
  'Practice Manager': 'Practice Manager',
  'Staff User': 'Staff User',
  'Front Desk': 'Staff User',
  'Read-Only Auditor': 'Read-Only Auditor',
};

type Session = { id: string; created_at: string; last_seen_at: string; ended_at: string | null;
  end_reason: string | null; user_agent: string | null; ip_address: string | null; active: boolean; current: boolean };

const errorText = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback);
const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : '—');

export const UserProfileView = () => {
  const [profileData, setProfileData] = useState({
    firstName: '',
    lastName: '',
    email: '',
    phone: '',
    role: '',
    clinic: ''
  });
  const [loadError, setLoadError] = useState('');
  const [saveState, setSaveState] = useState<{ ok: boolean; text: string } | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const [isPwOpen, setIsPwOpen] = useState(false);
  const [pw, setPw] = useState({ current: '', next: '', confirm: '' });
  const [pwState, setPwState] = useState<{ ok: boolean; text: string } | null>(null);
  const [minLength, setMinLength] = useState(8);

  const [sessions, setSessions] = useState<Session[]>([]);
  const [idleMinutes, setIdleMinutes] = useState<number | null>(null);

  const applyUser = useCallback((user: any, clinicName?: string) => {
    const nameParts = (user.full_name || '').trim().split(' ');
    setProfileData({
      firstName: nameParts[0] || '',
      lastName: nameParts.slice(1).join(' ') || '',
      email: user.email || '',
      phone: user.phone || '',
      role: ROLE_LABELS[user.role] || user.role || '',
      clinic: clinicName || ''
    });
  }, []);

  const loadSessions = useCallback(async () => {
    try {
      const res = await fetchWithAuth(`${API}/api/v1/auth/sessions`);
      const data = await res.json();
      setSessions(data.sessions || []);
      setIdleMinutes(data.idle_timeout_minutes ?? null);
    } catch { /* the session list is informational */ }
  }, []);

  React.useEffect(() => {
    (async () => {
      try {
        const res = await fetchWithAuth(`${API}/api/v1/auth/me`);
        const user = await res.json();
        let clinicName = '';
        if (user.clinic_id) {
          try {
            const c = await (await fetchWithAuth(`${API}/api/v1/clinics/${user.clinic_id}`)).json();
            clinicName = c.name || '';
          } catch { /* no clinic visible */ }
        }
        if (!clinicName && user.org_id) {
          try {
            const o = await (await fetchWithAuth(`${API}/api/v1/organizations/${user.org_id}`)).json();
            clinicName = o.name || '';
          } catch { /* platform users have no organization */ }
        }
        applyUser(user, clinicName || (user.role === 'System Administrator' ? 'Platform' : ''));
        localStorage.setItem('user', JSON.stringify(user));
      } catch (e) {
        setLoadError(errorText(e, 'Could not load your profile.'));
      }
      try {
        const p = await (await fetch(`${API}/api/v1/auth/password-policy`)).json();
        if (p?.min_length) setMinLength(p.min_length);
      } catch { /* default rule */ }
      loadSessions();
    })();
  }, [applyUser, loadSessions]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaveState(null);
    const fullName = `${profileData.firstName} ${profileData.lastName}`.trim();
    if (!fullName) {
      setSaveState({ ok: false, text: 'Name is required.' });
      return;
    }
    setIsSaving(true);
    try {
      const res = await fetchWithAuth(`${API}/api/v1/auth/me`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ full_name: fullName, phone: profileData.phone })
      });
      const user = await res.json();
      applyUser(user, profileData.clinic);
      localStorage.setItem('user', JSON.stringify(user));
      setSaveState({ ok: true, text: 'Profile updated successfully' });
    } catch (err) {
      setSaveState({ ok: false, text: errorText(err, 'Profile was not saved.') });
    } finally {
      setIsSaving(false);
    }
  };

  const handlePasswordChange = async (e: React.FormEvent) => {
    e.preventDefault();
    setPwState(null);
    if (pw.next !== pw.confirm) {
      setPwState({ ok: false, text: 'New password and confirmation do not match.' });
      return;
    }
    try {
      const res = await fetchWithAuth(`${API}/api/v1/auth/change-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: pw.current, new_password: pw.next, confirm_password: pw.confirm })
      });
      const data = await res.json();
      setPw({ current: '', next: '', confirm: '' });
      setPwState({ ok: true, text: `Password changed. ${data.other_sessions_ended || 0} other session(s) signed out.` });
      loadSessions();
    } catch (err) {
      setPwState({ ok: false, text: errorText(err, 'Password was not changed.') });
    }
  };

  const endSession = async (id: string) => {
    try {
      await fetchWithAuth(`${API}/api/v1/auth/sessions/${id}`, { method: 'DELETE' });
    } catch { /* shown by the refreshed list */ }
    loadSessions();
  };

  const inputClass = "w-full bg-white/5 border border-white/10 rounded-2xl px-4 py-3 text-sm font-bold text-white outline-none focus:border-[var(--color-surface-raised)] focus:ring-1 focus:ring-[var(--color-surface-raised)] transition-all";

  return (
    <div className="max-w-[1000px] mx-auto space-y-6 animate-in fade-in duration-500">

      {/* Header */}
      <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] p-8 rounded-[24px] border border-white/10 shadow-[0_20px_50px_rgba(46,16,85,0.3)] text-white relative overflow-hidden">
        <div className="absolute top-0 right-0 w-64 h-64 bg-[var(--color-text-primary)]0/5 rounded-full blur-[80px] pointer-events-none"></div>
        <div className="flex items-center gap-6 relative z-10">
          <div className="w-24 h-24 rounded-[20px] bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] p-1 shadow-xl">
            <div className="w-full h-full bg-white/5 rounded-[16px] flex items-center justify-center border-2 border-transparent">
              <User className="w-10 h-10 text-[var(--color-accent)]" />
            </div>
          </div>
          <div>
            <h2 className="text-3xl font-black text-white">{profileData.firstName} {profileData.lastName}</h2>
            <div className="flex items-center gap-3 mt-2">
              <span className="px-3 py-1 bg-[#EEF2FF] text-[var(--color-accent)] text-xs font-bold rounded-lg uppercase tracking-wider">
                {profileData.role}
              </span>
              <span className="text-sm font-bold text-white/70 flex items-center gap-1">
                <Building className="w-4 h-4" /> {profileData.clinic || '—'}
              </span>
            </div>
            {loadError && <p className="text-xs text-rose-300 font-bold mt-2">{loadError}</p>}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">

        {/* Left Col - Security */}
        <div className="md:col-span-1 space-y-6">
          <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] p-6 rounded-[24px] border border-white/10 shadow-[0_20px_50px_rgba(46,16,85,0.3)] text-white">
            <h3 className="text-lg font-bold text-white flex items-center gap-2 mb-6">
              <Shield className="w-5 h-5 text-[var(--color-accent)]" /> Account Security
            </h3>

            <div className="space-y-4">
              <div className="p-4 rounded-2xl border border-amber-300/30 bg-amber-500/10 flex items-start gap-3">
                <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-bold text-white">Two-Factor Authentication</p>
                  <p className="text-xs text-white/70 mt-1">Not enabled. Two-factor sign-in is not part of this release; your account uses email and password.</p>
                </div>
              </div>

              <button
                type="button"
                onClick={() => { setIsPwOpen(!isPwOpen); setPwState(null); }}
                aria-expanded={isPwOpen}
                className="w-full py-3 rounded-2xl border border-white/10 bg-white/5 text-sm font-bold text-white/60 hover:bg-white/5 hover:text-white transition-colors flex items-center justify-center gap-2"
              >
                <Lock className="w-4 h-4" /> Change Password
              </button>

              {isPwOpen && (
                <form onSubmit={handlePasswordChange} className="space-y-3" aria-label="Change password">
                  <input type="password" autoComplete="current-password" required placeholder="Current password"
                    aria-label="Current password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} className={inputClass} />
                  <input type="password" autoComplete="new-password" required placeholder="New password"
                    aria-label="New password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} className={inputClass} />
                  <input type="password" autoComplete="new-password" required placeholder="Confirm new password"
                    aria-label="Confirm new password" value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} className={inputClass} />
                  <p className="text-[11px] text-white/50">At least {minLength} characters, with letters and digits.</p>
                  {pwState && <p role="status" className={`text-xs font-bold ${pwState.ok ? 'text-emerald-400' : 'text-rose-300'}`}>{pwState.text}</p>}
                  <button type="submit" className="w-full py-2.5 rounded-2xl bg-[var(--color-surface-raised)] text-sm font-bold text-white">Update Password</button>
                </form>
              )}
            </div>
          </div>

          <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] p-6 rounded-[24px] border border-white/10 shadow-[0_20px_50px_rgba(46,16,85,0.3)] text-white">
            <h3 className="text-lg font-bold text-white flex items-center gap-2 mb-2">
              <Monitor className="w-5 h-5 text-[var(--color-accent)]" /> Sessions
            </h3>
            {idleMinutes !== null && <p className="text-[11px] text-white/50 mb-3">A session ends after {idleMinutes} minutes without activity.</p>}
            <div className="space-y-2">
              {sessions.length === 0 && <p className="text-xs text-white/50">No sessions recorded.</p>}
              {sessions.slice(0, 5).map((s) => (
                <div key={s.id} className="p-3 rounded-xl border border-white/10 bg-white/5 text-xs">
                  <div className="flex justify-between items-center gap-2">
                    <span className="font-bold text-white">{s.current ? 'This session' : s.active ? 'Active' : `Ended (${(s.end_reason || '').replace(/_/g, ' ')})`}</span>
                    {s.active && !s.current && (
                      <button type="button" onClick={() => endSession(s.id)} className="text-rose-300 font-bold hover:underline">Sign out</button>
                    )}
                  </div>
                  <p className="text-white/60 mt-1">Started {when(s.created_at)}</p>
                  <p className="text-white/60">Last active {when(s.ended_at || s.last_seen_at)}</p>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Right Col - Profile Form */}
        <div className="md:col-span-2">
          <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] p-8 rounded-[24px] border border-white/10 shadow-[0_20px_50px_rgba(46,16,85,0.3)] text-white">
            <h3 className="text-lg font-bold text-white mb-6">Personal Information</h3>

            <form onSubmit={handleSave} className="space-y-6">
              <div className="grid grid-cols-2 gap-6">
                <div>
                  <label className="text-[10px] font-extrabold text-white/50 uppercase tracking-wider block mb-2">First Name</label>
                  <input
                    type="text"
                    aria-label="First Name"
                    value={profileData.firstName}
                    onChange={(e) => setProfileData({...profileData, firstName: e.target.value})}
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className="text-[10px] font-extrabold text-white/50 uppercase tracking-wider block mb-2">Last Name</label>
                  <input
                    type="text"
                    aria-label="Last Name"
                    value={profileData.lastName}
                    onChange={(e) => setProfileData({...profileData, lastName: e.target.value})}
                    className={inputClass}
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-6">
                <div>
                  <label className="text-[10px] font-extrabold text-white/50 uppercase tracking-wider block mb-2">Email Address</label>
                  <div className="relative">
                    <Mail className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-white/50" />
                    <input
                      type="email"
                      value={profileData.email}
                      disabled
                      className="w-full bg-white/5 opacity-70 border border-white/10 rounded-2xl pl-11 pr-4 py-3 text-sm font-bold text-white/70 cursor-not-allowed"
                    />
                  </div>
                </div>
                <div>
                  <label className="text-[10px] font-extrabold text-white/50 uppercase tracking-wider block mb-2">Phone Number</label>
                  <div className="relative">
                    <Phone className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-white/50" />
                    <input
                      type="tel"
                      aria-label="Phone Number"
                      value={profileData.phone}
                      onChange={(e) => setProfileData({...profileData, phone: e.target.value})}
                      className="w-full bg-white/5 border border-white/10 rounded-2xl pl-11 pr-4 py-3 text-sm font-bold text-white outline-none focus:border-[var(--color-surface-raised)] focus:ring-1 focus:ring-[var(--color-surface-raised)] transition-all"
                    />
                  </div>
                </div>
              </div>

              <div className="pt-6 border-t border-white/10 flex items-center justify-between">
                {saveState ? (
                  <span role="status" className={`${saveState.ok ? 'text-emerald-600' : 'text-rose-300'} text-sm font-bold flex items-center gap-2`}>
                    {saveState.ok ? <CheckCircle className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />} {saveState.text}
                  </span>
                ) : (
                  <span></span>
                )}

                <button
                  type="submit"
                  disabled={isSaving}
                  className="bg-[var(--color-surface-raised)] hover:bg-[var(--color-surface)] text-white px-6 py-3 rounded-2xl text-sm font-bold shadow-lg shadow-[var(--color-text-primary)]0/25 transition-all hover:-translate-y-0.5 active:translate-y-0 disabled:opacity-60"
                >
                  {isSaving ? 'Saving…' : 'Save Changes'}
                </button>
              </div>
            </form>
          </div>
        </div>

      </div>
    </div>
  );
};
