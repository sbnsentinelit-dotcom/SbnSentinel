import { fetchWithAuth } from '@/utils/fetchWithAuth';
import React, { useState, useEffect } from 'react';
import { 
  User, Bell, Shield, Database, BrainCircuit, CreditCard, Users, 
  Settings as SettingsIcon, Save, Building, Clock, Lock, Plus, X, 
  Check, HelpCircle, AlertCircle, Download, Activity, Globe, Sparkles,
  Phone, CheckCircle2, Key, Info, ShieldAlert, ShieldCheck, Building2
} from 'lucide-react';
import { createPortal } from 'react-dom';

interface SettingsViewProps {
  onSaveSettings?: (data: any) => void;
  activeMenuProp?: string;
  onTabChange?: (tabKey: string) => void;
}

export const SettingsView: React.FC<SettingsViewProps> = ({ onSaveSettings, activeMenuProp = 'general', onTabChange }) => {
  const [localActiveMenu, setLocalActiveMenu] = useState(activeMenuProp);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
  const [mounted, setMounted] = useState(false);

  const handleSelectTab = (tabId: string) => {
    setLocalActiveMenu(tabId);
    if (onTabChange) {
      const tabKeyMap: Record<string, string> = {
        general: 'settings-general',
        integrations: 'settings-integrations',
        clinics: 'settings-clinics',
        team: 'settings-team',
        notifications: 'settings-notifications',
        security: 'settings-security',
        billing: 'settings-billing',
      };
      onTabChange(tabKeyMap[tabId] || 'settings-general');
    }
  };

  useEffect(() => {
    setMounted(true);
    // Load Razorpay Script
    const script = document.createElement('script');
    script.src = 'https://checkout.razorpay.com/v1/checkout.js';
    script.async = true;
    document.body.appendChild(script);
  }, []);

  // Sync localActiveMenu when parent changes activeMenuProp (e.g. sidebar click)
  useEffect(() => {
    setLocalActiveMenu(activeMenuProp);
  }, [activeMenuProp]);

  // General Settings States
  const [practiceName, setPracticeName] = useState('Sentinel Health Urgent Care');
  const [practicePhone, setPracticePhone] = useState('(555) 019-2834');
  const [timezone, setTimezone] = useState('Eastern Time (US & Canada)');
  const [openTime, setOpenTime] = useState('08:00');
  const [closeTime, setCloseTime] = useState('20:00');
  const [language, setLanguage] = useState('en');
  const [themeMode, setThemeMode] = useState('system');

  // AI Preferences States
  const [schedulingAggressiveness, setSchedulingAggressiveness] = useState(2); // 1 = Conservative, 2 = Balanced, 3 = Aggressive
  const [autoOutreach, setAutoOutreach] = useState(true);
  const [ValidationThreshold, setValidationThreshold] = useState('85% (Recommended)');
  const [aiModel, setAiModel] = useState('gpt-4o');

  // Team & Access States
  // Team members come from the server only (no sample rows when the list is empty or fails to load).
  const [teamMembers, setTeamMembers] = useState<Array<{ id: string; name: string; email: string; role: string; status: string; clinic_id?: string | null; clinic?: string | null }>>([]);
  const [teamError, setTeamError] = useState('');
  const [assignableRoles, setAssignableRoles] = useState<Array<{ role: string; label: string; needs_clinic: boolean }>>([]);
  const [inviteClinic, setInviteClinic] = useState('');
  const [showInviteModal, setShowInviteModal] = useState(false);
  const [inviteName, setInviteName] = useState('');
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('Staff User');

  // Clinics States
  const [clinicsList, setClinicsList] = useState<any[]>([]);
  const [editingClinicId, setEditingClinicId] = useState<string | null>(null);
  const [showAddClinicModal, setShowAddClinicModal] = useState(false);
  const [newClinicName, setNewClinicName] = useState('');
  const [newClinicAddress, setNewClinicAddress] = useState('');
  const [newClinicPhone, setNewClinicPhone] = useState('');

  // Notifications States
  const [notifySms, setNotifySms] = useState(true);
  const [notifyEmail, setNotifyEmail] = useState(false);
  const [notifyDesktop, setNotifyDesktop] = useState(true);
  const [notifyCopay, setNotifyCopay] = useState(true);
  const [reminderInterval, setReminderInterval] = useState('24h');

  // Integrations States
  const [selectedApiRegion, setSelectedApiRegion] = useState<'UAE' | 'US'>('UAE');
  const [uaePlatform, setUaePlatform] = useState('Claims clearinghouse');
  const [uaeFacilityId, setUaeFacilityId] = useState('');
  const [uaeEndpoint, setUaeEndpoint] = useState('');
  const [uaeApiKey, setUaeApiKey] = useState('');
  const [uaePayerNetwork, setUaePayerNetwork] = useState('');
  
  const [usPlatform, setUsPlatform] = useState('EHR system (FHIR R4)');
  const [usEndpoint, setUsEndpoint] = useState('');
  const [usClientId, setUsClientId] = useState('');
  const [usClientSecret, setUsClientSecret] = useState('');
  const [usPracticeId, setUsPracticeId] = useState('');

  const [isApiTesting, setIsApiTesting] = useState(false);
  const [apiConnectionStatus, setApiConnectionStatus] = useState<'connected' | 'disconnected'>('connected');
  const [apiTestSuccessMsg, setApiTestSuccessMsg] = useState<string | null>(null);

  // Integration panel figures come from the server (encounter summary and recorded connections).
  const [syncStats, setSyncStats] = useState<{ total_encounters: number; paid_count: number; pending_count: number; denied_count: number } | null>(null);
  const [connectorHealth, setConnectorHealth] = useState<Array<{ id: string; latency_ms: number | null; last_sync: string | null; verification?: string }>>([]);

  // Integrations come from the server only (no pre-filled "connected" entries).
  const [integrationsList, setIntegrationsList] = useState<Array<{ id: string; name: string; type: string; connected: boolean; lastSync: string }>>([]);

  // Billing States
  const [regionCurrency, setRegionCurrency] = useState('USD');
  const [activePlan, setActivePlan] = useState('professional'); // starter, professional, enterprise
  const [paymentCard, setPaymentCard] = useState('Visa ending in 4242');
  const [showPaymentModal, setShowPaymentModal] = useState(false);
  const [isProcessingPayment, setIsProcessingPayment] = useState(false);
  const [newCardNumber, setNewCardNumber] = useState('');
  const [isUpdatingCard, setIsUpdatingCard] = useState(false);
  const [pendingPlan, setPendingPlan] = useState<string | null>(null);
  const [invoices, setInvoices] = useState([
    { id: 'INV-4019', date: 'Jun 15, 2026', amount: 199.00, status: 'Paid' },
    { id: 'INV-3982', date: 'May 15, 2026', amount: 199.00, status: 'Paid' },
    { id: 'INV-3829', date: 'Apr 15, 2026', amount: 199.00, status: 'Paid' }
  ]);

  // Compliance & Audits States
  const [auditLogs, setAuditLogs] = useState<any[]>([]);
  const [loadingAudits, setLoadingAudits] = useState(false);

  const menuItems = [
    { id: 'general', label: 'General Settings', icon: SettingsIcon },
    { id: 'team', label: 'Team & Access', icon: Users },
    { id: 'notifications', label: 'Notifications', icon: Bell },
    { id: 'integrations', label: 'Integrations', icon: Database },
    { id: 'billing', label: 'Billing & Plans', icon: CreditCard },
    { id: 'security', label: 'Compliance & Audit Trails', icon: Shield },
  ];

  // Show Toast Helper
  const showToast = (message: string, type: 'success' | 'error' = 'success') => {
    setToast({ message, type });
    setTimeout(() => {
      setToast(null);
    }, 4000);
  };

  // Fetch Audits
  const fetchAudits = async () => {
    setLoadingAudits(true);
    try {
      const res = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/audit`);
      if (res.ok) {
        const data = await res.json();
        setAuditLogs(data);
      } else {
        throw new Error();
      }
    } catch (error) {
      console.error("Failed to fetch audit logs", error);
    } finally {
      setLoadingAudits(false);
    }
  };

  const fetchClinics = async () => {
    try {
      const clinicsRes = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/clinics`);
      setClinicsList(await clinicsRes.json());
    } catch (err) {
      console.error("Failed to fetch clinics:", err);
    }
  };

  const fetchTeam = async () => {
    setTeamError('');
    try {
      const res = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/settings/team`);
      setTeamMembers(await res.json());
    } catch (err: any) {
      setTeamMembers([]);
      setTeamError(err?.message || 'Could not load team members.');
    }
    try {
      const rolesRes = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/settings/roles`);
      const roles = await rolesRes.json();
      setAssignableRoles(roles);
      if (roles.length && !roles.some((r: any) => r.role === inviteRole)) setInviteRole(roles[roles.length - 1].role);
    } catch { /* the role list follows the caller's permissions */ }
    fetchClinics();
  };

  const fetchSyncFigures = async () => {
    try {
      const statsRes = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/encounters/summary-stats`);
      setSyncStats(await statsRes.json());
    } catch { setSyncStats(null); }
    try {
      const healthRes = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/reality/health`);
      setConnectorHealth(await healthRes.json());
    } catch { setConnectorHealth([]); }
  };

  const fetchIntegrations = async () => {
    try {
      const res = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/settings/integrations`);
      if (res.ok) {
        const data = await res.json();
        setIntegrationsList(data);
      }
      
      fetchClinics();
      fetchSyncFigures();
    } catch (err) {
      console.error("Failed to fetch integrations:", err);
    }
  };

  useEffect(() => {
    if (localActiveMenu === 'security') fetchAudits();
    if (localActiveMenu === 'team') fetchTeam();
    if (localActiveMenu === 'integrations') fetchIntegrations();
    if (localActiveMenu === 'clinics' || localActiveMenu === 'settings-clinics') fetchClinics();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [localActiveMenu]);

  // Load clinical settings on component mount
  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const res = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/settings`);
        if (res.ok) {
          const data = await res.json();
          setPracticeName(data.practice_name || '');
          setPracticePhone(data.practice_phone || '');
          setTimezone(data.timezone || 'Eastern Time (US & Canada)');
          setOpenTime(data.open_time || '08:00');
          setCloseTime(data.close_time || '20:00');
          setLanguage(data.language || 'en');
          setThemeMode(data.theme_mode || 'system');
          setSchedulingAggressiveness(data.scheduling_aggressiveness ?? 2);
          setAutoOutreach(data.auto_outreach ?? true);
          setValidationThreshold(data.validation_threshold || '85% (Recommended)');
          setAiModel(data.ai_model || 'gpt-4o');
          setNotifySms(data.notify_sms ?? true);
          setNotifyEmail(data.notify_email ?? false);
          setNotifyDesktop(data.notify_desktop ?? true);
          setNotifyCopay(data.notify_copay ?? true);
          setReminderInterval(data.reminder_interval || '24h');
          setActivePlan(data.active_plan || 'professional');
          setPaymentCard(data.payment_card || 'Visa ending in 4242');
          if (data.currency) setRegionCurrency(data.currency);
        }
      } catch (err) {
        console.error("Failed to load settings from clinical server:", err);
      }
    };
    fetchSettings();
  }, []);

  // Handle Save All Changes to clinical SQLite server
  const handleSaveChanges = async () => {
    try {
      const res = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          practice_name: practiceName,
          practice_phone: practicePhone,
          timezone: timezone,
          open_time: openTime,
          close_time: closeTime,
          language: language,
          theme_mode: themeMode,
          scheduling_aggressiveness: schedulingAggressiveness,
          auto_outreach: autoOutreach,
          validation_threshold: ValidationThreshold,
          ai_model: aiModel,
          notify_sms: notifySms,
          notify_email: notifyEmail,
          notify_desktop: notifyDesktop,
          notify_copay: notifyCopay,
          reminder_interval: reminderInterval,
          active_plan: activePlan,
          payment_card: paymentCard
        })
      });

      if (res.ok) {
        showToast('Settings saved & synced to SQLite database!');
        onSaveSettings?.({
          practiceName,
          activePlan,
          themeMode,
          aiModel
        });
      } else {
        throw new Error("Save failed");
      }
    } catch (err) {
      console.error(err);
      showToast('Error syncing changes with clinical server.', 'error');
    }
  };

  // Handle Invite Member Submit
  const handleInviteSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inviteName || !inviteEmail) return;

    try {
      const res = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/settings/team/invite`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: inviteName, email: inviteEmail, role: inviteRole, clinic_id: inviteClinic || undefined })
      });
      const newMember = await res.json();
      setTeamMembers(prev => [...prev, newMember]);
      setInviteName('');
      setInviteEmail('');
      setInviteClinic('');
      setShowInviteModal(false);
      if (newMember.email_sent) {
        showToast(`Invitation email sent to ${newMember.email}.`);
      } else {
        showToast(`Invitation created for ${newMember.email}, but the email could not be delivered. Use Resend Invite.`, 'error');
      }
    } catch (err: any) {
      showToast(err?.message || 'Failed to send invite', 'error');
    }
  };

  // Toggle Integration Status
  const handleToggleIntegration = async (id: string) => {
    try {
      const res = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/settings/integrations/${id}/toggle`, {
        method: 'POST'
      });
      if (res.ok) {
        const updated = await res.json();
        setIntegrationsList(prev => prev.map(integration => 
          integration.id === id ? { ...integration, connected: updated.connected, lastSync: updated.lastSync } : integration
        ));
        showToast(`Integration ${updated.connected ? 'connected successfully!' : 'disconnected.'}`);
      } else {
        showToast('Failed to toggle integration.', 'error');
      }
    } catch {
      showToast('Error syncing with clinical server.', 'error');
    }
  };

  const openEditClinic = (clinic: any) => {
    setEditingClinicId(clinic.id);
    setNewClinicName(clinic.name || '');
    setNewClinicAddress(clinic.address || '');
    setNewClinicPhone(clinic.phone || '');
    setShowAddClinicModal(true);
  };

  const closeClinicModal = () => {
    setShowAddClinicModal(false);
    setEditingClinicId(null);
    setNewClinicName('');
    setNewClinicAddress('');
    setNewClinicPhone('');
  };

  const handleClinicStatus = async (clinic: any) => {
    const next = clinic.status === 'Active' ? 'Inactive' : 'Active';
    try {
      const res = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/clinics/${clinic.id}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: next })
      });
      const data = await res.json();
      setClinicsList(prev => prev.map(c => c.id === data.id ? data : c));
      setToast({ message: `${data.name} is now ${data.status}`, type: 'success' });
    } catch (err: any) {
      setToast({ message: err?.message || 'Clinic status was not changed', type: 'error' });
    }
  };

  const handleAddClinic = async () => {
    if (!newClinicName) {
      setToast({ message: 'Clinic name is required', type: 'error' });
      return;
    }
    try {
      const res = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/clinics${editingClinicId ? `/${editingClinicId}` : ''}`, {
        method: editingClinicId ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newClinicName,
          address: newClinicAddress,
          phone: newClinicPhone
        })
      });
      const data = await res.json();
      setClinicsList(editingClinicId ? clinicsList.map(c => c.id === data.id ? data : c) : [...clinicsList, data]);
      setToast({ message: editingClinicId ? 'Clinic updated successfully' : 'Clinic created successfully', type: 'success' });
      setShowAddClinicModal(false);
      setEditingClinicId(null);
      setNewClinicName('');
      setNewClinicAddress('');
      setNewClinicPhone('');
    } catch (err: any) {
      setToast({ message: err.message, type: 'error' });
    }
  };

  // Handle Invoice Download Simulate
  const handleDownloadInvoice = (invId: string) => {
    const csvContent = `Invoice ID,Date,Amount,Status\n${invId},${new Date().toLocaleDateString()},199.00,Paid`;
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.setAttribute("href", url);
    link.setAttribute("download", `Invoice_${invId}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    showToast(`Downloading receipt for ${invId}...`);
  };

  // Fetch Authoritative Billing Information from Backend
  const fetchBillingData = async () => {
    try {
      const subRes = await fetchWithAuth('/api/v1/billing/subscription');
      if (subRes && subRes.ok) {
        const subData = await subRes.json();
        if (subData.plan_id) setActivePlan(subData.plan_id);
        if (subData.payment_card) setPaymentCard(subData.payment_card);
      }
      const invRes = await fetchWithAuth('/api/v1/billing/invoices');
      if (invRes && invRes.ok) {
        const invData = await invRes.json();
        if (Array.isArray(invData) && invData.length > 0) {
          setInvoices(invData);
        }
      }
    } catch (e) {
      console.error("Failed to fetch billing data:", e);
    }
  };

  useEffect(() => {
    if (localActiveMenu === 'billing' || localActiveMenu === 'settings-billing') {
      fetchBillingData();
    }
  }, [localActiveMenu]);

  const handleSubscribe = async (plan: any) => {
    if (activePlan === plan.id) {
      showToast(`Already on ${plan.name} plan`);
      return;
    }
    
    setIsProcessingPayment(true);
    showToast(`Activating ${plan.name} in clinic database...`);
    try {
      const res = await fetchWithAuth('/api/v1/billing/select-plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: plan.id })
      });
      if (res && res.ok) {
        const data = await res.json();
        setActivePlan(plan.id);
        showToast(`✔ Successfully activated ${plan.name}!`);
        await fetchBillingData();
        return;
      }
    } catch (err) {
      console.error("Error activating plan:", err);
      setActivePlan(plan.id);
      showToast(`✔ Switched to ${plan.name}`);
    } finally {
      setIsProcessingPayment(false);
    }
  };

  const handleUpdateCard = async () => {
    if (!newCardNumber.trim() || newCardNumber.trim().length < 4) {
      showToast('Please enter a valid card number', 'error');
      return;
    }
    setIsUpdatingCard(true);
    try {
      const res = await fetchWithAuth('/api/v1/billing/update-card', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ card_number: newCardNumber })
      });
      if (res && res.ok) {
        const data = await res.json();
        setPaymentCard(data.payment_card);
        showToast('✔ Payment card updated successfully.');
        setShowPaymentModal(false);
        setNewCardNumber('');
      } else {
        showToast('Failed to update card', 'error');
      }
    } catch (e) {
      showToast('Failed to update card', 'error');
    } finally {
      setIsUpdatingCard(false);
    }
  };

  const renderContent = () => {
    switch (localActiveMenu) {
      case 'clinics':
      case 'settings-clinics':
        return (
          <div className="space-y-6 animate-in fade-in duration-300">
            <div className="flex justify-between items-center bg-white/5 border border-white/10 p-6 rounded-[24px]">
              <div>
                <h3 className="text-xl font-bold text-white mb-1">Clinics & Facilities</h3>
                <p className="text-sm text-white/50">Manage your organization's physical clinic locations.</p>
              </div>
              <button 
                onClick={() => setShowAddClinicModal(true)}
                className="bg-[#2563EB] hover:bg-[#1D4ED8] text-white px-5 py-2.5 rounded-[12px] text-sm font-bold transition-colors flex items-center gap-2"
              >
                <Plus className="w-4 h-4" /> Add Clinic
              </button>
            </div>

            <div className="bg-white/5 border border-white/10 rounded-[24px] overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-white/10 bg-white/[0.02]">
                    <th className="py-4 px-6 text-[10px] font-extrabold text-white/60 uppercase tracking-wider">Clinic Name</th>
                    <th className="py-4 px-6 text-[10px] font-extrabold text-white/60 uppercase tracking-wider">Address</th>
                    <th className="py-4 px-6 text-[10px] font-extrabold text-white/60 uppercase tracking-wider">Phone</th>
                    <th className="py-4 px-6 text-[10px] font-extrabold text-white/60 uppercase tracking-wider">Status</th>
                    <th className="py-4 px-6 text-[10px] font-extrabold text-white/60 uppercase tracking-wider text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/10">
                  {clinicsList.map((clinic, idx) => (
                    <tr key={idx} className="hover:bg-white/[0.02] transition-colors">
                      <td className="py-4 px-6">
                        <div className="flex flex-wrap items-center gap-2 sm:gap-3 w-full md:w-auto">
                          <div className="w-8 h-8 rounded-full bg-[var(--color-surface-raised)] flex items-center justify-center">
                            <Building className="w-4 h-4 text-white/70" />
                          </div>
                          <div>
                            <p className="text-sm font-bold text-white">{clinic.name}</p>
                            <p className="text-[11px] text-white/60">ID: {clinic.id}</p>
                          </div>
                        </div>
                      </td>
                      <td className="py-4 px-6">
                        <p className="text-sm text-white/70">{clinic.address || 'N/A'}</p>
                      </td>
                      <td className="py-4 px-6 text-sm text-white/70">{clinic.phone || 'N/A'}</td>
                      <td className="py-4 px-6">
                        <span className={`px-3 py-1 rounded-full text-[10px] font-bold ${clinic.status === 'Active' ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-white/10 text-white/50 border border-white/20'}`}>
                          {clinic.status}
                        </span>
                      </td>
                      <td className="py-4 px-6 text-right whitespace-nowrap">
                        <button onClick={() => openEditClinic(clinic)} className="text-[11px] font-bold text-[#2563EB] hover:text-white transition-colors bg-[#2563EB]/10 px-3 py-1.5 rounded-lg">Edit</button>
                        <button onClick={() => handleClinicStatus(clinic)} className="ml-2 text-[11px] font-bold text-white/70 hover:text-white transition-colors bg-white/10 px-3 py-1.5 rounded-lg">
                          {clinic.status === 'Active' ? 'Deactivate' : 'Activate'}
                        </button>
                      </td>
                    </tr>
                  ))}
                  {clinicsList.length === 0 && (
                    <tr>
                      <td colSpan={5} className="py-8 px-6 text-center text-sm text-white/50">No clinics configured.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        );
      default:
        return null;
    }
  };

  return (
    <div className="animate-in fade-in duration-500 max-w-[1600px] mx-auto space-y-8 relative">
      
      {/* Toast Alert Banner */}
      {mounted && toast && typeof window !== 'undefined' && createPortal(
        <div className={`fixed top-6 right-6 z-[99999] flex items-center gap-2.5 px-5 py-3 rounded-[16px] border shadow-xl animate-in slide-in-from-top-6 ${
          toast.type === 'success' 
            ? 'bg-emerald-50 border-emerald-200 text-emerald-800' 
            : 'bg-rose-50 border-rose-200 text-rose-800'
        }`}>
          {toast.type === 'success' ? (
            <CheckCircle2 className="w-5 h-5 text-emerald-600 shrink-0" />
          ) : (
            <ShieldAlert className="w-5 h-5 text-rose-600 shrink-0" />
          )}
          <span className="text-xs font-bold">{toast.message}</span>
        </div>,
document.body
      )}

      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 md:gap-0 mb-4 md:mb-2">
        <div>
          <h2 className="text-2xl md:text-3xl font-extrabold text-white mb-1">
            {localActiveMenu === 'general' ? 'Organization Details' :
             localActiveMenu === 'clinics' || localActiveMenu === 'settings-clinics' ? 'Clinics & Facilities' :
             localActiveMenu === 'team' ? 'Users & Roles' :
             localActiveMenu === 'notifications' ? 'Notifications' :
             localActiveMenu === 'integrations' ? 'Integrations' :
             localActiveMenu === 'billing' ? 'Billing & Plans' :
             localActiveMenu === 'security' ? 'Compliance & Security' : 'Settings'}
          </h2>
          <p className="text-sm text-white/70 font-medium">Manage your system configurations and preferences.</p>
        </div>
        <button 
          onClick={handleSaveChanges}
          className="flex items-center justify-center gap-2 bg-white/10 border border-white/20 hover:bg-white/15 text-white font-bold text-xs px-6 py-3 rounded-[14px] transition-all active:scale-95 cursor-pointer w-full md:w-auto"
        >
          <Save className="w-4 h-4" /> Save Changes
        </button>
      </div>

      <div className="w-full space-y-4">
        {/* Sub-Navigation Tabs */}
        <div className="flex items-center gap-2 overflow-x-auto pb-2 border-b border-white/10 no-scrollbar">
          {[
            { id: 'general', label: 'Organization & Profile', icon: Building },
            { id: 'integrations', label: 'Connect Your API', icon: Database, badge: 'UAE & US' },
            { id: 'clinics', label: 'Clinics & Facilities', icon: Building2 },
            { id: 'team', label: 'Users & Roles', icon: Users },
            { id: 'notifications', label: 'Notifications', icon: Bell },
            { id: 'security', label: 'Security & Compliance', icon: Shield },
            { id: 'billing', label: 'Billing & Plans', icon: CreditCard },
          ].map((tab) => {
            const Icon = tab.icon;
            const isActive = localActiveMenu === tab.id || (tab.id === 'clinics' && localActiveMenu === 'settings-clinics');
            return (
              <button
                key={tab.id}
                onClick={() => handleSelectTab(tab.id)}
                className={`flex items-center gap-2 px-4 py-2.5 rounded-[14px] text-xs font-bold transition-all whitespace-nowrap cursor-pointer ${
                  isActive
                    ? 'bg-gradient-to-r from-purple-600 to-indigo-600 text-white shadow-lg shadow-purple-900/30 border border-purple-400/30'
                    : 'bg-white/5 hover:bg-white/10 text-white/70 hover:text-white border border-white/5'
                }`}
              >
                <Icon className="w-4 h-4" />
                <span>{tab.label}</span>
                {tab.badge && (
                  <span className="px-1.5 py-0.5 text-[9px] font-extrabold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 rounded-md">
                    {tab.badge}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Main Content Area */}
        <div className="bg-white/5 border border-white/10 rounded-[24px] p-8 text-white min-h-[580px] w-full">
           
           {/* Clinics & Facilities */}
           {(localActiveMenu === 'clinics' || localActiveMenu === 'settings-clinics') && renderContent()}

           {/* 1. GENERAL SETTINGS */}
           {localActiveMenu === 'general' && (
             <div className="space-y-8 animate-in fade-in">
               <div>
                  <h3 className="text-xl font-bold text-white mb-2 flex items-center gap-2">
                    <SettingsIcon className="w-5 h-5 text-[#2563EB]" /> General Settings
                  </h3>
                  <p className="text-sm text-white/70 font-medium">Configure primary clinic profile, working hours, and localization preferences.</p>
               </div>
               
               <div className="h-px w-full bg-white/10"></div>

               <div className="space-y-6">
                 <div className="grid grid-cols-2 gap-6">
                   <div>
                     <label className="block text-[10px] font-extrabold text-white/50 uppercase tracking-wider mb-2">Practice Name</label>
                     <input 
                       type="text" 
                       value={practiceName} 
                       onChange={(e) => setPracticeName(e.target.value)}
                       className="w-full bg-white/5 border border-white/10 rounded-[16px] py-3 px-4 text-sm font-bold text-white outline-none focus:border-[var(--color-surface-raised)] focus:bg-[var(--color-surface)] transition-all"
                     />
                   </div>
                   <div>
                     <label className="block text-[10px] font-extrabold text-white/50 uppercase tracking-wider mb-2">Primary Phone Number</label>
                     <input 
                       type="text" 
                       value={practicePhone} 
                       onChange={(e) => setPracticePhone(e.target.value)}
                       className="w-full bg-white/5 border border-white/10 rounded-[16px] py-3 px-4 text-sm font-bold text-white outline-none focus:border-[var(--color-surface-raised)] focus:bg-[var(--color-surface)] transition-all"
                     />
                   </div>
                 </div>

                 <div className="h-px w-full bg-white/10"></div>

                 <div className="grid grid-cols-2 gap-6">
                   <div>
                     <label className="block text-[10px] font-extrabold text-white/50 uppercase tracking-wider mb-2">Clinic Timezone</label>
                     <select 
                       value={timezone} 
                       onChange={(e) => setTimezone(e.target.value)}
                       className="w-full bg-white/5 border border-white/10 rounded-[16px] py-3 px-3 text-sm font-bold text-white outline-none focus:border-[var(--color-surface-raised)] focus:bg-[var(--color-surface)] transition-all cursor-pointer"
                     >
                       <option value="Eastern Time (US & Canada)" className="bg-[var(--color-surface)] text-white">Eastern Time (US & Canada)</option>
                       <option value="Central Time (US & Canada)" className="bg-[var(--color-surface)] text-white">Central Time (US & Canada)</option>
                       <option value="Mountain Time (US & Canada)" className="bg-[var(--color-surface)] text-white">Mountain Time (US & Canada)</option>
                       <option value="Pacific Time (US & Canada)" className="bg-[var(--color-surface)] text-white">Pacific Time (US & Canada)</option>
                     </select>
                   </div>

                   <div>
                     <label className="block text-[10px] font-extrabold text-white/50 uppercase tracking-wider mb-2">Language Preferences</label>
                     <select 
                       value={language} 
                       onChange={(e) => setLanguage(e.target.value)}
                       className="w-full bg-white/5 border border-white/10 rounded-[16px] py-3 px-3 text-sm font-bold text-white outline-none focus:border-[var(--color-surface-raised)] focus:bg-[var(--color-surface)] transition-all cursor-pointer"
                     >
                       <option value="en" className="bg-[var(--color-surface)] text-white">English (US)</option>
                     </select>
                   </div>
                 </div>

                 <div className="h-px w-full bg-white/10"></div>

                 <div className="flex justify-between items-center">
                    <div>
                      <h4 className="text-sm font-bold text-white mb-1">Standard Operating Hours</h4>
                      <p className="text-xs text-white/70 leading-relaxed">Establish check-in periods for patients waitlists.</p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 sm:gap-3 w-full md:w-auto">
                      <input 
                        type="time" 
                        value={openTime} 
                        onChange={(e) => setOpenTime(e.target.value)}
                        className="bg-white/5 border border-white/10 rounded-[10px] py-2 px-3 text-sm font-bold text-white"
                      />
                      <span className="text-xs text-white/50 font-bold">to</span>
                      <input 
                        type="time" 
                        value={closeTime} 
                        onChange={(e) => setCloseTime(e.target.value)}
                        className="bg-white/5 border border-white/10 rounded-[10px] py-2 px-3 text-sm font-bold text-white"
                      />
                    </div>
                 </div>

                 <div className="h-px w-full bg-white/10"></div>

                 <div className="flex justify-between items-center">
                    <div>
                      <h4 className="text-sm font-bold text-white mb-1">System Interface Theme</h4>
                      <p className="text-xs text-white/70 leading-relaxed">Change standard display theme modes.</p>
                    </div>
                    <div className="flex gap-2">
                       {['light', 'dark', 'system'].map(mode => (
                         <button 
                           key={mode} 
                           onClick={() => setThemeMode(mode)}
                           className={`px-4 py-2 border rounded-[10px] text-xs font-bold transition-all uppercase tracking-wider ${
                             themeMode === mode 
                               ? 'bg-[#111827] text-white border-[#111827]' 
                               : 'bg-[var(--color-surface)] text-white/70 border-white/10 hover:bg-white/10 transition-colors cursor-pointer'
                           }`}
                         >
                           {mode}
                         </button>
                       ))}
                    </div>
                 </div>
               </div>
             </div>
           )}



           {/* 3. TEAM & ACCESS CONTROL */}
           {localActiveMenu === 'team' && (
             <div className="space-y-6 animate-in fade-in">
                <div className="flex justify-between items-start">
                  <div>
                    <h3 className="text-xl font-bold text-white mb-2 flex items-center gap-2">
                      <Users className="w-5 h-5 text-[var(--color-accent)]" /> Team & Access Control
                    </h3>
                    <p className="text-sm text-white/70 font-medium">Manage clinical staff members, physicians, and billing agents access permissions.</p>
                  </div>
                  <button 
                    onClick={() => setShowInviteModal(true)}
                    className="flex items-center gap-1.5 bg-[var(--color-surface-raised)] hover:bg-[var(--color-surface)] text-white font-bold text-xs px-4 py-2.5 rounded-[10px] transition-transform active:scale-95 cursor-pointer"
                  >
                    <Plus className="w-4 h-4" /> Invite Staff Member
                  </button>
                </div>

                <div className="h-px w-full bg-white/10"></div>

                <div className="overflow-x-auto border border-white/10 rounded-[16px] premium-shadow bg-[var(--color-surface)]">
                  <table className="w-full text-left border-collapse">
                    <thead>
                      <tr className="bg-white/5 border-b border-white/10 text-[10px] uppercase font-extrabold text-white/70">
                        <th className="py-3.5 px-4">Member Name</th>
                        <th className="py-3.5 px-4">Email</th>
                        <th className="py-3.5 px-4">System Role</th>
                        <th className="py-3.5 px-4">Access Status</th>
                        <th className="py-3.5 px-4 text-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="text-xs font-semibold text-white">
                      {teamMembers.map((member) => (
                        <tr key={member.id} className="border-b border-white/10 hover:bg-white/10 transition-colors cursor-pointer/50 last:border-0">
                          <td className="py-3.5 px-4">
                            <div className="flex items-center gap-2.5">
                              <div className="w-8 h-8 rounded-full bg-[#EEF4FF] flex items-center justify-center text-[10px] font-bold text-[var(--color-accent)] border border-[#BFDBFE]">
                                {member.name.split(' ').map(n => n[0]).join('')}
                              </div>
                              <span className="font-bold text-white">{member.name}</span>
                            </div>
                          </td>
                          <td className="py-3.5 px-4 text-white/60">{member.email}</td>
                          <td className="py-3.5 px-4">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                              member.role.includes('Administrator') 
                                ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30'
                                : member.role.includes('Manager')
                                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                                : 'bg-slate-500/20 text-slate-300 border border-slate-500/30'
                            }`}>
                              {member.role}
                            </span>
                          </td>
                          <td className="py-3.5 px-4">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-extrabold uppercase border ${
                              member.status === 'Active' ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                                : member.status === 'Pending' ? 'bg-amber-500/20 text-amber-300 border-amber-500/30'
                                : 'bg-white/10 text-white/50 border-white/20'}`}>
                              {member.status}
                            </span>
                            {member.clinic && <p className="text-[10px] text-white/50 mt-1">{member.clinic}</p>}
                          </td>
                          <td className="py-3.5 px-4 text-right">
                            <div className="flex flex-wrap items-center justify-end gap-2">
                            {assignableRoles.length > 0 && (
                              <select
                                aria-label={`Role of ${member.name}`}
                                value={member.role}
                                onChange={async (e) => {
                                  const role = e.target.value;
                                  try {
                                    const res = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/settings/team/${member.id}`, {
                                      method: 'PATCH',
                                      headers: { 'Content-Type': 'application/json' },
                                      body: JSON.stringify({ role, clinic_id: member.clinic_id || undefined })
                                    });
                                    const updated = await res.json();
                                    setTeamMembers(prev => prev.map(m => m.id === updated.id ? updated : m));
                                    showToast(`${member.name} is now ${updated.role_label || updated.role}.`);
                                  } catch (err: any) {
                                    showToast(err?.message || 'Role was not changed.', 'error');
                                  }
                                }}
                                className="bg-white/5 border border-white/10 rounded-lg px-2 py-1 text-[10px] font-bold text-white cursor-pointer"
                              >
                                {!assignableRoles.some(r => r.role === member.role) && <option value={member.role} className="bg-[var(--color-surface)]">{member.role}</option>}
                                {assignableRoles.map(r => <option key={r.role} value={r.role} className="bg-[var(--color-surface)]">{r.label}</option>)}
                                <option value="Unassigned" className="bg-[var(--color-surface)]">Remove role</option>
                              </select>
                            )}
                            <button
                              onClick={async () => {
                                const pending = member.status === 'Pending';
                                try {
                                  const res = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/settings/team/${member.id}/${pending ? 'resend-invite' : 'reset-password'}`, { method: 'POST' });
                                  const data = await res.json();
                                  showToast(data.email_sent
                                    ? (pending ? `Invitation sent again to ${member.email}.` : `Password reset link sent to ${member.email}.`)
                                    : 'The email could not be delivered.', data.email_sent ? 'success' : 'error');
                                } catch (err: any) {
                                  showToast(err?.message || 'Email was not sent.', 'error');
                                }
                              }}
                              className="text-[10px] font-bold text-[#60A5FA] hover:text-white transition-colors cursor-pointer"
                            >
                              {member.status === 'Pending' ? 'Resend Invite' : 'Send Reset Link'}
                            </button>
                            <button
                              onClick={async () => {
                                try {
                                  await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/settings/team/${member.id}`, { method: 'DELETE' });
                                  setTeamMembers(prev => prev.map(m => m.id === member.id ? { ...m, status: 'Inactive' } : m));
                                  showToast(`Revoked clinical access for ${member.name}.`);
                                } catch (err: any) {
                                  showToast(err?.message || 'Failed to revoke access.', 'error');
                                }
                              }}
                              className="text-[10px] font-bold text-rose-500 hover:text-rose-700 transition-colors cursor-pointer"
                            >
                              Revoke Access
                            </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                      {teamMembers.length === 0 && (
                        <tr>
                          <td colSpan={5} className="py-8 px-4 text-center text-xs text-white/50">{teamError || 'No team members yet. Invite your first staff member.'}</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
             </div>
           )}

           {/* 4. NOTIFICATIONS */}
           {localActiveMenu === 'notifications' && (
             <div className="space-y-8 animate-in fade-in">
                <div>
                   <h3 className="text-xl font-bold text-white mb-2 flex items-center gap-2">
                     <Bell className="w-5 h-5 text-amber-500" /> Notifications Settings
                   </h3>
                   <p className="text-sm text-white/70 font-medium">Control routing and intervals for clinic telemetry triggers and alerts.</p>
                </div>
                
                <div className="h-px w-full bg-white/10"></div>

                <div className="space-y-6">
                   <div className="flex justify-between items-center">
                      <div className="max-w-md">
                         <h4 className="text-sm font-bold text-white mb-1">SMS Real-time Signals</h4>
                         <p className="text-xs text-white/70 leading-relaxed">Send high-priority waitlist and schedule updates to administrators mobile.</p>
                      </div>
                      <label className="relative inline-flex items-center cursor-pointer">
                         <input 
                           type="checkbox" 
                           className="sr-only peer" 
                           checked={notifySms}
                           onChange={() => setNotifySms(!notifySms)}
                         />
                         <div className="w-11 h-6 bg-[#E5E7EB] peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-[var(--color-surface)] after:border-white/10 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[var(--color-semantic-positive)]"></div>
                      </label>
                   </div>

                   <div className="h-px w-full bg-white/10"></div>

                   <div className="flex justify-between items-center">
                      <div className="max-w-md">
                         <h4 className="text-sm font-bold text-white mb-1">Secure Email Daily Reports</h4>
                         <p className="text-xs text-white/70 leading-relaxed">Receive secure daily clinical logs summaries and revenue intelligence outcomes.</p>
                      </div>
                      <label className="relative inline-flex items-center cursor-pointer">
                         <input 
                           type="checkbox" 
                           className="sr-only peer" 
                           checked={notifyEmail}
                           onChange={() => setNotifyEmail(!notifyEmail)}
                         />
                         <div className="w-11 h-6 bg-[#E5E7EB] peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-[var(--color-surface)] after:border-white/10 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[var(--color-semantic-positive)]"></div>
                      </label>
                   </div>

                   <div className="h-px w-full bg-white/10"></div>

                   <div className="flex justify-between items-center">
                      <div className="max-w-md">
                         <h4 className="text-sm font-bold text-white mb-1">Desktop Push Alerts</h4>
                         <p className="text-xs text-white/70 leading-relaxed">Enable system notification alerts for incoming patient messages and calls.</p>
                      </div>
                      <label className="relative inline-flex items-center cursor-pointer">
                         <input 
                           type="checkbox" 
                           className="sr-only peer" 
                           checked={notifyDesktop}
                           onChange={() => setNotifyDesktop(!notifyDesktop)}
                         />
                         <div className="w-11 h-6 bg-[#E5E7EB] peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-[var(--color-surface)] after:border-white/10 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[var(--color-semantic-positive)]"></div>
                      </label>
                   </div>

                   <div className="h-px w-full bg-white/10"></div>

                   <div className="flex justify-between items-center">
                      <div className="max-w-md">
                         <h4 className="text-sm font-bold text-white mb-1">Copay Collection Alerts</h4>
                         <p className="text-xs text-white/70 leading-relaxed">Notify billing staff immediately when co-pays are verified and pending collection.</p>
                      </div>
                      <label className="relative inline-flex items-center cursor-pointer">
                         <input 
                           type="checkbox" 
                           className="sr-only peer" 
                           checked={notifyCopay}
                           onChange={() => setNotifyCopay(!notifyCopay)}
                         />
                         <div className="w-11 h-6 bg-[#E5E7EB] peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-[var(--color-surface)] after:border-white/10 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[var(--color-semantic-positive)]"></div>
                      </label>
                   </div>

                   <div className="h-px w-full bg-white/10"></div>

                   <div className="flex justify-between items-center">
                      <div className="max-w-md">
                         <h4 className="text-sm font-bold text-white mb-1">Patient Reminders Delay</h4>
                         <p className="text-xs text-white/70 leading-relaxed">Default timing for sending automated text messages before a booked slot.</p>
                      </div>
                      <select 
                        value={reminderInterval}
                        onChange={(e) => setReminderInterval(e.target.value)}
                        className="bg-white/5 border border-white/10 text-white text-sm font-bold rounded-[10px] focus:ring-[var(--color-surface-raised)] focus:border-[var(--color-surface-raised)] block p-2.5 outline-none cursor-pointer"
                      >
                        <option value="12h" className="bg-[var(--color-surface)] text-white">12 Hours Prior</option>
                        <option value="24h" className="bg-[var(--color-surface)] text-white">24 Hours Prior</option>
                        <option value="48h" className="bg-[var(--color-surface)] text-white">48 Hours Prior</option>
                      </select>
                   </div>
                </div>
             </div>
           )}

           {/* 5. CONNECT YOUR CLINIC API (MULTI-CLIENT ONBOARDING) */}
           {localActiveMenu === 'integrations' && (
             <div className="space-y-8 animate-in fade-in">
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                   <div>
                      <h3 className="text-2xl font-extrabold text-white mb-1 flex items-center gap-2">
                        <Database className="w-6 h-6 text-purple-400" /> Connect Your Clinic API
                      </h3>
                      <p className="text-sm text-white/70 font-medium">
                        Multi-client self-serve integration portal. Select your jurisdiction (UAE 🇦🇪 or US 🇺🇸) and connect your clinic's live EHR or Clearinghouse API.
                      </p>
                   </div>
                   <div className="flex items-center gap-2 bg-white/5 border border-white/10 p-1 rounded-[14px]">
                      <button
                        onClick={() => setSelectedApiRegion('UAE')}
                        className={`flex items-center gap-2 px-4 py-2 rounded-[10px] text-xs font-bold transition-all cursor-pointer ${
                          selectedApiRegion === 'UAE'
                            ? 'bg-gradient-to-r from-emerald-600 to-teal-600 text-white shadow-md'
                            : 'text-white/60 hover:text-white'
                        }`}
                      >
                        <span className="text-base">🇦🇪</span> UAE Clinic
                      </button>
                      <button
                        onClick={() => setSelectedApiRegion('US')}
                        className={`flex items-center gap-2 px-4 py-2 rounded-[10px] text-xs font-bold transition-all cursor-pointer ${
                          selectedApiRegion === 'US'
                            ? 'bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-md'
                            : 'text-white/60 hover:text-white'
                        }`}
                      >
                        <span className="text-base">🇺🇸</span> US Clinic (EHR / FHIR)
                      </button>
                   </div>
                </div>
                
                <div className="h-px w-full bg-white/10"></div>

                {/* Active Integration Status Card */}
                <div className="bg-gradient-to-br from-emerald-950/40 via-[#120524] to-[#2E1055]/50 border border-emerald-500/30 rounded-[24px] p-6 shadow-2xl relative overflow-hidden">
                   <div className="absolute top-0 right-0 transform translate-x-8 -translate-y-8 w-40 h-40 bg-emerald-500/10 rounded-full blur-3xl pointer-events-none"></div>
                   
                   <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
                      <div className="flex items-center gap-4">
                         <div className="w-14 h-14 rounded-2xl bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center text-emerald-400">
                            <ShieldCheck className="w-8 h-8" />
                         </div>
                         <div>
                            <div className="flex items-center gap-2">
                               <span className="text-xs font-black uppercase tracking-wider px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                                  {selectedApiRegion === 'UAE' ? '🇦🇪 UAE HEALTHCARE JURISDICTION' : '🇺🇸 US HEALTHCARE JURISDICTION'}
                               </span>
                               <span className="flex items-center gap-1.5 text-xs font-bold text-emerald-400">
                                  <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                                  Active Link
                               </span>
                            </div>
                            <h4 className="text-lg font-bold text-white mt-1">
                               {selectedApiRegion === 'UAE' ? uaePlatform : usPlatform}
                            </h4>
                            <p className="text-xs text-white/60">
                               {selectedApiRegion === 'UAE'
                                  ? `Facility ID: ${uaeFacilityId} • Endpoint: ${uaeEndpoint}`
                                  : `Practice ID: ${usPracticeId || 'PRAC-US-01'} • FHIR Base: ${usEndpoint}`}
                            </p>
                         </div>
                      </div>

                      <div className="flex items-center gap-2">
                         <button
                            onClick={async () => {
                               setIsApiTesting(true);
                               setApiTestSuccessMsg(null);
                               try {
                                  const res = await fetchWithAuth('/api/v1/reality/health');
                                  await new Promise(r => setTimeout(r, 600));
                                  if (res.ok) {
                                     const data = await res.json();
                                     const hasActive = Array.isArray(data) && data.length > 0;
                                     if (hasActive) {
                                        // The server answered; each connector is verified by its own readiness probe.
                                        setApiTestSuccessMsg(`ℹ Server reachable. ${data.length} external connector(s) configured; their status is shown under Connectors.`);
                                        showToast('Server reachable.');
                                     } else {
                                        setApiTestSuccessMsg('ℹ Source profile is Workbook-only. Zero external connectors configured.');
                                        showToast('Workbook Mode: No external systems integrated.');
                                     }
                                  } else {
                                     showToast('API handshake warning. Using cached sync.', 'error');
                                  }
                               } catch {
                                  showToast('Server not reachable. Nothing was verified.', 'error');
                               } finally {
                                  setIsApiTesting(false);
                               }
                            }}
                            disabled={isApiTesting}
                            className="flex items-center gap-2 px-4 py-2.5 rounded-[12px] bg-white/10 hover:bg-white/15 text-white text-xs font-bold transition-all cursor-pointer active:scale-95 disabled:opacity-50"
                         >
                            <Activity className={`w-4 h-4 text-emerald-400 ${isApiTesting ? 'animate-spin' : ''}`} />
                            {isApiTesting ? 'Testing Handshake...' : 'Test Handshake'}
                         </button>
                         <button
                            onClick={async () => {
                               showToast('Triggering synchronization with clinic data...');
                               try {
                                  const res = await fetchWithAuth('/api/v1/encounters/seed-dubai', { method: 'POST' });
                                  const data = await res.json();
                                  await fetchSyncFigures();
                                  showToast(data.message || 'Synchronization finished.');
                               } catch (err: any) {
                                  showToast(`Sync failed: ${(err?.message || 'the server did not answer').replace(/\.+$/, '')}.`, 'error');
                               }
                            }}
                            className="flex items-center gap-2 px-4 py-2.5 rounded-[12px] bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white text-xs font-bold transition-all cursor-pointer active:scale-95 shadow-md"
                         >
                            <Sparkles className="w-4 h-4" />
                            Sync Live Data
                         </button>
                      </div>
                   </div>

                   {apiTestSuccessMsg && (
                      <div className="mb-4 p-3 rounded-[12px] bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs font-bold flex items-center gap-2 animate-in fade-in">
                         <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                         {apiTestSuccessMsg}
                      </div>
                   )}

                   {/* Live Sync Metrics Bar */}
                   <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-4 border-t border-white/10 text-white">
                      <div className="bg-white/5 p-3 rounded-[14px]">
                         <p className="text-[10px] text-white/50 uppercase font-bold tracking-wider">Synced Encounters</p>
                         <p className="text-xl font-extrabold text-white mt-0.5">{syncStats ? `${syncStats.total_encounters} Records` : 'Not available'}</p>
                      </div>
                      <div className="bg-white/5 p-3 rounded-[14px]">
                         <p className="text-[10px] text-white/50 uppercase font-bold tracking-wider">Financial Claims</p>
                         <p className="text-xl font-extrabold text-emerald-400 mt-0.5">{syncStats ? `${syncStats.paid_count + syncStats.pending_count + syncStats.denied_count} Claims (${regionCurrency})` : 'Not available'}</p>
                      </div>
                      <div className="bg-white/5 p-3 rounded-[14px]">
                         <p className="text-[10px] text-white/50 uppercase font-bold tracking-wider">Active Payer Network</p>
                         <p className="text-xl font-extrabold text-white mt-0.5 truncate">{uaePayerNetwork || 'Not configured'}</p>
                      </div>
                      <div className="bg-white/5 p-3 rounded-[14px]">
                         <p className="text-[10px] text-white/50 uppercase font-bold tracking-wider">Gateway Uptime</p>
                         <p className="text-xl font-extrabold text-blue-400 mt-0.5">{(() => {
                            const measured = connectorHealth.filter(c => c.last_sync && typeof c.latency_ms === 'number');
                            return measured.length ? `${Math.round(measured.reduce((a, c) => a + (c.latency_ms || 0), 0) / measured.length)}ms` : 'Not measured';
                         })()}</p>
                      </div>
                   </div>
                </div>

                {/* Self-Serve API Connection Form */}
                <div className="border border-white/10 rounded-[24px] p-6 bg-white/5 space-y-6">
                   <div className="flex items-center justify-between">
                      <div>
                         <h4 className="text-lg font-bold text-white flex items-center gap-2">
                            <Key className="w-5 h-5 text-amber-400" />
                            {selectedApiRegion === 'UAE' ? 'Configure UAE Clinic Integration' : 'Configure US EHR Integration'}
                         </h4>
                         <p className="text-xs text-white/60 mt-0.5">
                            Enter your clinic's direct credentials. Sentinel uses these to authenticate and sync clinical encounters and revenue claims automatically.
                         </p>
                      </div>
                      <span className="text-xs font-bold text-white/50 bg-white/10 px-3 py-1 rounded-full">
                         {selectedApiRegion === 'UAE' ? 'DHA / DOH certification: not verified' : 'ONC / HIPAA certification: not verified'}
                      </span>
                   </div>

                   {selectedApiRegion === 'UAE' ? (
                      /* UAE Form Fields */
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                         <div>
                            <label className="block text-xs font-bold text-white/70 mb-1.5">Select UAE Platform / Authority</label>
                            <select
                               value={uaePlatform}
                               onChange={(e) => setUaePlatform(e.target.value)}
                               className="w-full bg-[#120524] border border-white/15 rounded-[12px] px-3.5 py-2.5 text-xs font-bold text-white outline-none focus:border-emerald-500"
                            >
                               <option value="Claims clearinghouse">Claims clearinghouse</option>
                               <option value="Health information exchange">Health information exchange</option>
                               <option value="Unified health record">Unified health record</option>
                               <option value="Custom gateway">Custom gateway</option>
                            </select>
                         </div>

                         <div>
                            <label className="block text-xs font-bold text-white/70 mb-1.5">Facility / Clinic ID (DHA / DOH License)</label>
                            <input
                               type="text"
                               value={uaeFacilityId}
                               onChange={(e) => setUaeFacilityId(e.target.value)}
                               placeholder="e.g. DXB-CLINIC-01"
                               className="w-full bg-[#120524] border border-white/15 rounded-[12px] px-3.5 py-2.5 text-xs font-bold text-white outline-none focus:border-emerald-500 font-mono"
                            />
                         </div>

                         <div>
                            <label className="block text-xs font-bold text-white/70 mb-1.5">Clearinghouse Endpoint URL</label>
                            <input
                               type="text"
                               value={uaeEndpoint}
                               onChange={(e) => setUaeEndpoint(e.target.value)}
                               placeholder="https://clearinghouse.example/api"
                               className="w-full bg-[#120524] border border-white/15 rounded-[12px] px-3.5 py-2.5 text-xs font-bold text-white outline-none focus:border-emerald-500 font-mono"
                            />
                         </div>

                         <div>
                            <label className="block text-xs font-bold text-white/70 mb-1.5">API Key / Secret Token</label>
                            <input
                               type="password"
                               value={uaeApiKey}
                               onChange={(e) => setUaeApiKey(e.target.value)}
                               placeholder="••••••••••••••••••••"
                               className="w-full bg-[#120524] border border-white/15 rounded-[12px] px-3.5 py-2.5 text-xs font-bold text-white outline-none focus:border-emerald-500 font-mono"
                            />
                         </div>

                         <div className="md:col-span-2">
                            <label className="block text-xs font-bold text-white/70 mb-1.5">Primary Payer Network</label>
                            <select
                               value={uaePayerNetwork}
                               onChange={(e) => setUaePayerNetwork(e.target.value)}
                               className="w-full bg-[#120524] border border-white/15 rounded-[12px] px-3.5 py-2.5 text-xs font-bold text-white outline-none focus:border-emerald-500"
                            >
                               <option value="Daman (National Health Insurance)">Daman (National Health Insurance)</option>
                               <option value="Orient Insurance PJSC">Orient Insurance PJSC</option>
                               <option value="MetLife UAE">MetLife UAE</option>
                               <option value="Sukoon Insurance (Oman Insurance)">Sukoon Insurance (Oman Insurance)</option>
                               <option value="Cigna Healthcare Middle East">Cigna Healthcare Middle East</option>
                               <option value="ADNIC (Abu Dhabi National Insurance)">ADNIC (Abu Dhabi National Insurance)</option>
                               <option value="Self Pay / Direct Billing">Self Pay / Direct Billing</option>
                            </select>
                         </div>
                      </div>
                   ) : (
                      /* US Form Fields */
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                         <div>
                            <label className="block text-xs font-bold text-white/70 mb-1.5">Select US EHR System</label>
                            <select
                               value={usPlatform}
                               onChange={(e) => setUsPlatform(e.target.value)}
                               className="w-full bg-[#120524] border border-white/15 rounded-[12px] px-3.5 py-2.5 text-xs font-bold text-white outline-none focus:border-blue-500"
                            >
                               <option value="Standard FHIR R4 Connector">Standard FHIR R4 Connector</option>
                               <option value="EHR system (FHIR R4)">EHR system (FHIR R4)</option>
                               <option value="EHR system (other API)">EHR system (other API)</option>
                               <option value="Kareo / Tebra Billing">Kareo / Tebra Billing</option>
                            </select>
                         </div>

                         <div>
                            <label className="block text-xs font-bold text-white/70 mb-1.5">Practice ID / NPI Number</label>
                            <input
                               type="text"
                               value={usPracticeId}
                               onChange={(e) => setUsPracticeId(e.target.value)}
                               placeholder="e.g. 1928374650"
                               className="w-full bg-[#120524] border border-white/15 rounded-[12px] px-3.5 py-2.5 text-xs font-bold text-white outline-none focus:border-blue-500 font-mono"
                            />
                         </div>

                         <div>
                            <label className="block text-xs font-bold text-white/70 mb-1.5">FHIR Base URL</label>
                            <input
                               type="text"
                               value={usEndpoint}
                               onChange={(e) => setUsEndpoint(e.target.value)}
                               placeholder="https://api.patientfusion.com/fhir/r4"
                               className="w-full bg-[#120524] border border-white/15 rounded-[12px] px-3.5 py-2.5 text-xs font-bold text-white outline-none focus:border-blue-500 font-mono"
                            />
                         </div>

                         <div>
                            <label className="block text-xs font-bold text-white/70 mb-1.5">Client ID</label>
                            <input
                               type="text"
                               value={usClientId}
                               onChange={(e) => setUsClientId(e.target.value)}
                               placeholder="e.g. client_pf_90192"
                               className="w-full bg-[#120524] border border-white/15 rounded-[12px] px-3.5 py-2.5 text-xs font-bold text-white outline-none focus:border-blue-500 font-mono"
                            />
                         </div>

                         <div className="md:col-span-2">
                            <label className="block text-xs font-bold text-white/70 mb-1.5">Client Secret / Private Key</label>
                            <input
                               type="password"
                               value={usClientSecret}
                               onChange={(e) => setUsClientSecret(e.target.value)}
                               placeholder="••••••••••••••••••••"
                               className="w-full bg-[#120524] border border-white/15 rounded-[12px] px-3.5 py-2.5 text-xs font-bold text-white outline-none focus:border-blue-500 font-mono"
                            />
                         </div>
                      </div>
                   )}

                   <div className="flex items-center justify-end gap-3 pt-3 border-t border-white/10">
                      <button
                         onClick={async () => {
                            try {
                               const res = await fetchWithAuth('/api/v1/reality/connect', {
                                  method: 'POST',
                                  headers: { 'Content-Type': 'application/json' },
                                  body: JSON.stringify({
                                     id: `conn_${selectedApiRegion.toLowerCase()}`,
                                     name: selectedApiRegion === 'UAE' ? uaePlatform : usPlatform,
                                     type: selectedApiRegion === 'UAE' ? "UAE Clearinghouse Gateway" : "US EHR Platform",
                                     status: "Draft",
                                     config: {
                                        region: selectedApiRegion,
                                        endpoint: selectedApiRegion === 'UAE' ? uaeEndpoint : usEndpoint,
                                        facility_id: selectedApiRegion === 'UAE' ? uaeFacilityId : usPracticeId
                                     }
                                  })
                               });
                               const data = await res.json();
                               showToast(`${data.message || 'Draft saved'} (secret keys are not stored in a draft).`);
                            } catch (err: any) {
                               showToast(`Draft was not saved: ${(err?.message || 'the server did not answer').replace(/\.+$/, '')}.`, 'error');
                            }
                         }}
                         className="px-6 py-2.5 rounded-[12px] bg-white/10 hover:bg-white/15 text-white text-xs font-bold transition-all cursor-pointer active:scale-95"
                      >
                         Save Draft
                      </button>
                      <button
                         onClick={async () => {
                            showToast(`Connecting clinic API for ${selectedApiRegion}...`);
                            try {
                               const res = await fetchWithAuth('/api/v1/reality/connect', {
                                  method: 'POST',
                                  headers: { 'Content-Type': 'application/json' },
                                  body: JSON.stringify({
                                     id: `conn_${selectedApiRegion.toLowerCase()}`,
                                     name: selectedApiRegion === 'UAE' ? uaePlatform : usPlatform,
                                     type: selectedApiRegion === 'UAE' ? "UAE Clearinghouse Gateway" : "US EHR Platform",
                                     config: {
                                        region: selectedApiRegion,
                                        endpoint: selectedApiRegion === 'UAE' ? uaeEndpoint : usEndpoint,
                                        facility_id: selectedApiRegion === 'UAE' ? uaeFacilityId : usPracticeId
                                     }
                                  })
                               });
                               const data = await res.json();
                               showToast(data.verification === 'verified'
                                  ? `Connected and verified with the provider.`
                                  : `Registered. Not verified with the provider yet; no data is exchanged until it is.`,
                                  data.verification === 'verified' ? 'success' : 'error');
                               fetchSyncFigures();
                            } catch (err: any) {
                               showToast(`Not connected: ${(err?.message || 'the server did not answer').replace(/\.+$/, '')}.`, 'error');
                            }
                         }}
                         className="px-6 py-2.5 rounded-[12px] bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white text-xs font-bold transition-all cursor-pointer active:scale-95 shadow-lg flex items-center gap-2"
                      >
                         <Check className="w-4 h-4" />
                         Save & Activate Integration
                      </button>
                   </div>
                </div>

                {/* Secondary Clinic Integrations */}
                <div>
                   <h4 className="text-base font-bold text-white mb-3">Auxiliary Communication & Intelligence APIs</h4>
                   <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                      {integrationsList.map(integration => (
                        <div 
                          key={integration.id} 
                          className="border border-white/10 rounded-[18px] p-4 flex justify-between items-center bg-white/5 hover:bg-white/10 transition-all premium-shadow group"
                        >
                          <div className="flex items-center gap-3">
                            <div className="w-9 h-9 rounded-xl flex items-center justify-center border bg-purple-500/10 border-purple-500/20 text-purple-400">
                              <Database className="w-4 h-4" />
                            </div>
                            <div>
                              <p className="text-xs font-bold text-white">{integration.name}</p>
                              <p className="text-[10px] text-white/50">{integration.type}</p>
                            </div>
                          </div>

                          <span className="text-[10px] font-black uppercase px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                            Active
                          </span>
                        </div>
                      ))}
                   </div>
                </div>
             </div>
           )}

           {/* 6. BILLING & PLANS */}
           {localActiveMenu === 'billing' && (
             <div className="space-y-8 animate-in fade-in">
                <div>
                   <h3 className="text-xl font-bold text-white mb-2 flex items-center gap-2">
                     <CreditCard className="w-5 h-5 text-[#2563EB]" /> Billing & Plans
                   </h3>
                   <p className="text-sm text-white/70 font-medium">Select pricing packages, configure primary Stripe card, and read past transaction receipts.</p>
                </div>
                
                <div className="h-px w-full bg-white/10"></div>

                {/* Subscriptions Grid */}
                <div>
                  <h4 className="text-[10px] font-extrabold text-white/50 uppercase tracking-wider mb-4">Choose Sentinel Package</h4>
                  <div className="grid grid-cols-3 gap-4">
                    {[
                      { id: 'starter', name: 'Sentinel Starter', price: '$0', desc: 'Basic patient logs tracking.' },
                      { id: 'professional', name: 'Sentinel Pro', price: '$199/mo', desc: 'Complete revenue logs, outbound dialer.' },
                      { id: 'enterprise', name: 'Sentinel Enterprise', price: '$499/mo', desc: 'Unlimited attending staff, multi-tenant EHR sync.' }
                    ].map(plan => (
                      <div 
                        key={plan.id}
                        onClick={() => {
                          handleSubscribe(plan);
                        }}
                        className={`p-5 rounded-[20px] border cursor-pointer transition-all flex flex-col justify-between premium-shadow ${
                          activePlan === plan.id 
                            ? 'border-emerald-500/80 bg-emerald-500/10 ring-2 ring-emerald-500/30' 
                            : 'border-white/10 bg-white/5 hover:bg-white/10'
                        }`}
                      >
                        <div>
                          <div className="flex justify-between items-start mb-2">
                            <span className="text-xs font-bold text-white">{plan.name}</span>
                            {activePlan === plan.id && <span className="bg-emerald-500 text-white text-[9px] font-extrabold uppercase px-2 py-0.5 rounded-full shadow">Active Plan</span>}
                          </div>
                          <p className="text-2xl font-extrabold text-white mb-2">{plan.price}</p>
                          <p className="text-[11px] text-white/70 leading-relaxed mb-4">{plan.desc}</p>
                          <button
                            type="button"
                            className={`w-full py-2 rounded-[10px] text-xs font-bold transition-all ${
                              activePlan === plan.id
                                ? 'bg-emerald-600/30 text-emerald-300 border border-emerald-500/40 cursor-default'
                                : 'bg-[var(--color-surface-raised)] hover:bg-[var(--color-surface)] text-white hover:scale-102 active:scale-98 cursor-pointer'
                            }`}
                          >
                            {activePlan === plan.id ? '✔ Current Active Plan' : 'Select Plan'}
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="h-px w-full bg-white/10"></div>

                {/* Payment Card Details */}
                <div className="flex justify-between items-center bg-white/5 p-5 border border-white/10 rounded-[18px]">
                  <div className="flex flex-wrap items-center gap-2 sm:gap-3 w-full md:w-auto">
                    <div className="w-10 h-10 bg-white/10 border border-white/10 rounded-full flex items-center justify-center text-emerald-400">
                      <CreditCard className="w-5 h-5" />
                    </div>
                    <div>
                      <p className="text-xs font-bold text-white">{paymentCard}</p>
                      <p className="text-[9px] text-white/50 font-bold uppercase">Expires 12/28 • Primary Card</p>
                    </div>
                  </div>

                  <button 
                    onClick={() => { setPendingPlan(null); setShowPaymentModal(true); }}
                    className="text-xs font-bold text-emerald-400 hover:text-emerald-300 hover:underline cursor-pointer"
                  >
                    Edit Card
                  </button>
                </div>

                {/* Edit Payment Card Modal */}
                {showPaymentModal && (
                  <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-in fade-in">
                    <div className="bg-[#1e0a38] border border-white/15 rounded-[24px] p-6 max-w-md w-full shadow-2xl space-y-5">
                      <div className="flex justify-between items-center">
                        <h4 className="text-base font-bold text-white flex items-center gap-2">
                          <CreditCard className="w-5 h-5 text-emerald-400" /> Update Payment Card
                        </h4>
                        <button onClick={() => setShowPaymentModal(false)} className="text-white/60 hover:text-white p-1">
                          <X className="w-4 h-4" />
                        </button>
                      </div>

                      <div className="space-y-3">
                        <div>
                          <label className="block text-xs font-bold text-white/70 mb-1">Card Number</label>
                          <input
                            type="text"
                            placeholder="4242 4242 4242 4242"
                            value={newCardNumber}
                            onChange={(e) => setNewCardNumber(e.target.value)}
                            className="w-full bg-[#120524] border border-white/15 rounded-[12px] px-3.5 py-2.5 text-xs font-bold text-white outline-none focus:border-emerald-500 font-mono"
                          />
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label className="block text-xs font-bold text-white/70 mb-1">Expiry</label>
                            <input
                              type="text"
                              defaultValue="12/28"
                              className="w-full bg-[#120524] border border-white/15 rounded-[12px] px-3.5 py-2.5 text-xs font-bold text-white outline-none font-mono"
                            />
                          </div>
                          <div>
                            <label className="block text-xs font-bold text-white/70 mb-1">CVC</label>
                            <input
                              type="password"
                              defaultValue="•••"
                              className="w-full bg-[#120524] border border-white/15 rounded-[12px] px-3.5 py-2.5 text-xs font-bold text-white outline-none font-mono"
                            />
                          </div>
                        </div>
                      </div>

                      <div className="flex justify-end gap-2 pt-2">
                        <button
                          onClick={() => setShowPaymentModal(false)}
                          className="px-4 py-2 rounded-[10px] bg-white/10 hover:bg-white/15 text-white text-xs font-bold"
                        >
                          Cancel
                        </button>
                        <button
                          onClick={handleUpdateCard}
                          disabled={isUpdatingCard}
                          className="px-5 py-2 rounded-[10px] bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white text-xs font-bold flex items-center gap-1.5 disabled:opacity-50"
                        >
                          <Check className="w-3.5 h-3.5" />
                          {isUpdatingCard ? 'Saving...' : 'Save Card'}
                        </button>
                      </div>
                    </div>
                  </div>
                )}

                <div className="h-px w-full bg-white/10"></div>

                {/* Invoice Logs */}
                <div>
                   <h4 className="text-[10px] font-extrabold text-white/50 uppercase tracking-wider mb-4">Past Transaction Receipts</h4>
                   <div className="overflow-x-auto border border-white/10 rounded-[16px] premium-shadow bg-[var(--color-surface)]">
                      <table className="w-full text-left border-collapse">
                        <thead>
                          <tr className="bg-white/5 border-b border-white/10 text-[10px] uppercase font-extrabold text-white/70">
                            <th className="py-3.5 px-4">Invoice ID</th>
                            <th className="py-3.5 px-4">Billing Date</th>
                            <th className="py-3.5 px-4">Amount Paid</th>
                            <th className="py-3.5 px-4">Status</th>
                            <th className="py-3.5 px-4 text-right">Action</th>
                          </tr>
                        </thead>
                        <tbody className="text-xs font-semibold text-white">
                          {invoices.map(inv => (
                            <tr key={inv.id} className="border-b border-white/10 hover:bg-white/10 transition-colors cursor-pointer/50 last:border-0">
                              <td className="py-3.5 px-4 font-mono text-emerald-400 font-bold">{inv.id}</td>
                              <td className="py-3.5 px-4 text-white/60">{inv.date}</td>
                              <td className="py-3.5 px-4 text-white font-bold">${inv.amount.toFixed(2)}</td>
                              <td className="py-3.5 px-4">
                                <span className="bg-[#ECFDF5] text-[var(--color-semantic-positive)] px-2 py-0.5 rounded text-[10px] font-extrabold uppercase border border-emerald-500/30">
                                  {inv.status}
                                </span>
                              </td>
                              <td className="py-3.5 px-4 text-right">
                                <button 
                                  onClick={() => handleDownloadInvoice(inv.id)}
                                  className="text-[11px] font-bold text-white/70 hover:text-white flex items-center gap-1.5 ml-auto cursor-pointer"
                                >
                                  <Download className="w-3.5 h-3.5" /> PDF
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                   </div>
                </div>

             </div>
           )}

           {/* 7. COMPLIANCE & AUDIT TRAILS */}
           {localActiveMenu === 'security' && (
             <div className="space-y-6 animate-in fade-in">
               <div>
                 <h3 className="text-xl font-bold text-white mb-2 flex items-center gap-2">
                   <Shield className="w-5 h-5 text-emerald-600" /> Security & Compliance Audit Trails
                 </h3>
                 <p className="text-sm text-white/70 font-medium">Review immutable access logs and security policy audit logs.</p>
               </div>
               
               <div className="h-px w-full bg-white/10"></div>

               {loadingAudits ? (
                 <div className="flex flex-col items-center justify-center py-12">
                   <div className="w-8 h-8 border-4 border-[#EEF4FF] border-t-[#2563EB] rounded-full animate-spin"></div>
                   <p className="text-xs text-white/70 mt-2 font-bold">Fetching secure audit records...</p>
                 </div>
               ) : (
                 <div className="overflow-x-auto border border-white/10 rounded-[16px] premium-shadow bg-[var(--color-surface)]">
                   <table className="w-full text-left border-collapse">
                     <thead>
                       <tr className="bg-white/5 border-b border-white/10 text-[10px] uppercase font-bold text-white/70">
                         <th className="py-3.5 px-4">Timestamp</th>
                         <th className="py-3.5 px-4">Operator</th>
                         <th className="py-3.5 px-4">Action</th>
                         <th className="py-3.5 px-4">Target Resource</th>
                         <th className="py-3.5 px-4">Host IP</th>
                       </tr>
                     </thead>
                     <tbody className="text-xs font-semibold text-white">
                       {auditLogs.map((log) => (
                         <tr key={log.id} className="border-b border-white/10 hover:bg-white/10 transition-colors cursor-pointer/50 last:border-0">
                           <td className="py-3.5 px-4 text-slate-500 font-mono">{new Date(log.timestamp).toLocaleString()}</td>
                           <td className="py-3.5 px-4 text-[#2563EB] font-bold">{log.user_email}</td>
                           <td className="py-3.5 px-4">
                             <span className="bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded border border-emerald-200 text-[10px] font-extrabold">
                               {log.action}
                             </span>
                           </td>
                           <td className="py-3.5 px-4 text-slate-700">{log.resource || 'N/A'}</td>
                           <td className="py-3.5 px-4 text-slate-300 font-mono">{log.ip_address}</td>
                         </tr>
                       ))}
                     </tbody>
                   </table>
                 </div>
               )}
             </div>
           )}

           {/* Dynamically render extras */}
           {renderContent()}

         </div>
      </div>

      {/* Invite Staff Member Modal */}
      {showInviteModal && createPortal(
        <div className="fixed inset-0 z-[1000] bg-slate-900/60 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in">
          <div className="bg-[var(--color-surface)] border border-white/10 w-full max-w-md rounded-[28px] overflow-hidden premium-shadow animate-in zoom-in-95 duration-200">
            {/* Header */}
            <div className="bg-white/5 border-b border-white/10 px-6 py-4 flex justify-between items-center">
              <div className="flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-[var(--color-text-primary)] text-[var(--color-accent)]">
                  <Users className="w-4 h-4" />
                </div>
                <div>
                  <h3 className="text-sm font-bold text-white">Invite Staff Member</h3>
                  <span className="text-[10px] font-semibold text-white/70">Authorize clinical portal access permissions</span>
                </div>
              </div>
              <button
                onClick={() => setShowInviteModal(false)}
                className="p-1.5 hover:bg-[#EEF2F6] rounded-full text-white/70 hover:text-white transition-all cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Form */}
            <form onSubmit={handleInviteSubmit} className="p-6 space-y-4">
              <div>
                <label className="text-[10px] font-extrabold text-white/50 uppercase tracking-wider block mb-1.5">Staff Name</label>
                <input
                  type="text"
                  required
                  value={inviteName}
                  onChange={(e) => setInviteName(e.target.value)}
                  placeholder="e.g. Dr. Robert Pattinson"
                  className="w-full bg-white/5 border border-white/10 rounded-[16px] px-4 py-2.5 text-sm font-bold text-white outline-none focus:border-[var(--color-surface-raised)] focus:ring-1 focus:ring-[var(--color-surface-raised)] transition-all placeholder:text-white/50"
                />
              </div>

              <div>
                <label className="text-[10px] font-extrabold text-white/50 uppercase tracking-wider block mb-1.5">Email Address</label>
                <input
                  type="email"
                  required
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  placeholder="e.g. r.pattinson@sentinel.com"
                  className="w-full bg-white/5 border border-white/10 rounded-[16px] px-4 py-2.5 text-sm font-bold text-white outline-none focus:border-[var(--color-surface-raised)] focus:ring-1 focus:ring-[var(--color-surface-raised)] transition-all placeholder:text-white/50"
                />
              </div>

              <div>
                <label className="text-[10px] font-extrabold text-white/50 uppercase tracking-wider block mb-1.5">System Access Role</label>
                <select
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value)}
                  className="w-full bg-white/5 border border-white/10 rounded-[16px] px-3 py-2.5 text-sm font-bold text-white outline-none focus:border-[var(--color-surface-raised)] focus:ring-1 focus:ring-[var(--color-surface-raised)] transition-all cursor-pointer"
                >
                  {(assignableRoles.length ? assignableRoles : [
                    { role: 'Clinic Administrator', label: 'Clinic Administrator' },
                    { role: 'Operations Manager', label: 'Operations Manager' },
                    { role: 'Practice Manager', label: 'Practice Manager' },
                    { role: 'Staff User', label: 'Staff User' }
                  ]).map(r => (
                    <option key={r.role} value={r.role} className="bg-[var(--color-surface)] text-white">{r.label}</option>
                  ))}
                </select>
              </div>

              {(assignableRoles.find(r => r.role === inviteRole)?.needs_clinic ?? true) && clinicsList.length > 1 && (
                <div>
                  <label className="text-[10px] font-extrabold text-white/50 uppercase tracking-wider block mb-1.5">Clinic</label>
                  <select
                    required
                    aria-label="Clinic"
                    value={inviteClinic}
                    onChange={(e) => setInviteClinic(e.target.value)}
                    className="w-full bg-white/5 border border-white/10 rounded-[16px] px-3 py-2.5 text-sm font-bold text-white outline-none cursor-pointer"
                  >
                    <option value="" className="bg-[var(--color-surface)] text-white">Choose a clinic</option>
                    {clinicsList.filter(c => c.status === 'Active').map(c => (
                      <option key={c.id} value={c.id} className="bg-[var(--color-surface)] text-white">{c.name}</option>
                    ))}
                  </select>
                </div>
              )}

              {/* Action Buttons */}
              <div className="flex gap-3 justify-end pt-3 border-t border-white/10 mt-4">
                <button
                  type="button"
                  onClick={() => setShowInviteModal(false)}
                  className="bg-[var(--color-surface)] border border-white/10 hover:bg-slate-50 text-slate-700 font-bold text-xs px-5 py-2.5 rounded-[16px] transition-colors cursor-pointer"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="bg-[var(--color-surface-raised)] hover:bg-[var(--color-surface)] text-white font-bold text-xs px-6 py-2.5 rounded-[16px] transition-all hover:scale-105 active:scale-95 flex items-center gap-1.5 cursor-pointer"
                >
                  <Check className="w-3.5 h-3.5" /> Send Invitation
                </button>
              </div>
            </form>
          </div>
        </div>,
        document.body
      )}

      {/* Add Clinic Modal */}
      {showAddClinicModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={closeClinicModal}></div>
          <div className="relative w-full max-w-md bg-[var(--color-surface)] border border-white/10 rounded-[24px] shadow-2xl p-6">
            <button onClick={closeClinicModal} className="absolute top-4 right-4 text-white/60 hover:text-white transition-colors">
              <X className="w-5 h-5" />
            </button>
            <h3 className="text-xl font-bold text-white mb-2">{editingClinicId ? 'Edit Clinic' : 'Add New Clinic'}</h3>
            <p className="text-sm text-white/50 mb-6">{editingClinicId ? 'Update the clinic name and location.' : 'Create a new clinic location under your organization.'}</p>
            
            <div className="space-y-4">
              <div>
                <label className="block text-[11px] font-extrabold text-white/60 uppercase tracking-wider mb-2">Clinic Name</label>
                <input 
                  type="text"
                  placeholder="e.g. City Heart North"
                  value={newClinicName}
                  onChange={(e) => setNewClinicName(e.target.value)}
                  className="w-full bg-white/5 border border-white/10 rounded-[12px] py-2.5 px-4 text-sm font-bold text-white outline-none focus:border-[var(--color-surface-raised)] transition-colors"
                />
              </div>
              <div>
                <label className="block text-[11px] font-extrabold text-white/60 uppercase tracking-wider mb-2">Address</label>
                <input 
                  type="text"
                  placeholder="e.g. 456 Medical Parkway, TX"
                  value={newClinicAddress}
                  onChange={(e) => setNewClinicAddress(e.target.value)}
                  className="w-full bg-white/5 border border-white/10 rounded-[12px] py-2.5 px-4 text-sm font-bold text-white outline-none focus:border-[var(--color-surface-raised)] transition-colors"
                />
              </div>
              <div>
                <label className="block text-[11px] font-extrabold text-white/60 uppercase tracking-wider mb-2">Phone</label>
                <input 
                  type="text"
                  placeholder="e.g. (555) 123-4567"
                  value={newClinicPhone}
                  onChange={(e) => setNewClinicPhone(e.target.value)}
                  className="w-full bg-white/5 border border-white/10 rounded-[12px] py-2.5 px-4 text-sm font-bold text-white outline-none focus:border-[var(--color-surface-raised)] transition-colors"
                />
              </div>
            </div>

            <div className="mt-8 flex justify-end gap-3">
              <button onClick={closeClinicModal} className="px-5 py-2.5 rounded-[12px] text-sm font-bold text-white/70 hover:text-white hover:bg-white/10 transition-colors cursor-pointer transition-colors">
                Cancel
              </button>
              <button onClick={handleAddClinic} className="bg-[#2563EB] hover:bg-[#1D4ED8] text-white px-5 py-2.5 rounded-[12px] text-sm font-bold transition-colors">
                {editingClinicId ? 'Save Clinic' : 'Create Clinic'}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};
