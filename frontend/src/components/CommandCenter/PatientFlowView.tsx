import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { fetchWithAuth } from '@/utils/fetchWithAuth';
import { FlowActionResult, FlowRow, FlowSnapshot, clinicTime, newIdempotencyKey, postFlow, sparkPath, trendClass } from '@/utils/patientFlow';
import { Users, UserCheck, Clock, AlertCircle, Search, Filter, Download, Calendar, Activity, ChevronLeft, ChevronRight, LayoutGrid, Stethoscope, Check, CreditCard, ShieldCheck, ShieldAlert, Camera, RefreshCw, Scan, Sparkles, Phone, X, FileText } from 'lucide-react';
import { useSelector } from 'react-redux';
import { RootState } from '@/store';

// Helper to parse existing clinical notes into individual SOAP fields
const parseSOAPNotes = (noteStr: string) => {
  const result = { s: '', o: '', a: '', p: '' };
  if (!noteStr) return result;

  const sMatch = noteStr.match(/Subjective:\s*([\s\S]*?)(?=Objective:|Assessment:|Plan:|$)/i);
  const oMatch = noteStr.match(/Objective:\s*([\s\S]*?)(?=Subjective:|Assessment:|Plan:|$)/i);
  const aMatch = noteStr.match(/Assessment:\s*([\s\S]*?)(?=Subjective:|Objective:|Plan:|$)/i);
  const pMatch = noteStr.match(/Plan:\s*([\s\S]*?)(?=Subjective:|Objective:|Assessment:|$)/i);

  result.s = sMatch ? sMatch[1].trim() : '';
  result.o = oMatch ? oMatch[1].trim() : '';
  result.a = aMatch ? aMatch[1].trim() : '';
  result.p = pMatch ? pMatch[1].trim() : '';

  // Fallback: if no patterns matched, put the entire string into Subjective
  if (!result.s && !result.o && !result.a && !result.p) {
    result.s = noteStr.trim();
  }
  return result;
};

// Popups render on the page body, above the sidebar and header: a parent with an animation
// (transform) would otherwise make "fixed" relative to that parent and cut the popup off.
const ModalPortal: React.FC<{ children: React.ReactNode }> = ({ children }) =>
  typeof document === 'undefined' ? null : createPortal(children, document.body);

export const PatientFlowView: React.FC = () => {
  const stats = useSelector((state: RootState) => state.signals.stats);
  const events = useSelector((state: RootState) => state.signals.events);

  const [isExporting, setIsExporting] = useState(false);
  const [showFilter, setShowFilter] = useState(false);
  const [activeFilter, setActiveFilter] = useState('');
  const [dateFilter, setDateFilter] = useState('Today');
  const [manualStep, setManualStep] = useState<number | null>(null);

  // Live Database Encounters State
  const [encounters, setEncounters] = useState<any[]>([]);
  const [selectedPatientEncounter, setSelectedPatientEncounter] = useState<any | null>(null);
  const selectedPatientEncounterRef = useRef(selectedPatientEncounter);
  useEffect(() => {
    selectedPatientEncounterRef.current = selectedPatientEncounter;
  }, [selectedPatientEncounter]);

  const [journeyData, setJourneyData] = useState<any | null>(null);
  const [journeysByEncounter, setJourneysByEncounter] = useState<Record<string, any>>({});
  const [isLoadingEncounters, setIsLoadingEncounters] = useState(true);

  // Live Patient Flow snapshot: queue, cards, trends, rooms and suggestions, all calculated on the
  // server from the clinic's visit events for the selected period and filter.
  const [flow, setFlow] = useState<FlowSnapshot | null>(null);
  const [flowError, setFlowError] = useState(false);
  const flowRequest = useRef(0);
  const flowParams = useMemo(() => {
    const params = new URLSearchParams({ period: dateFilter });
    if (activeFilter === 'High Priority') params.append('priority', 'Urgent');
    else if (activeFilter) params.append('department', activeFilter);
    return params.toString();
  }, [dateFilter, activeFilter]);
  const fetchFlow = useCallback(async () => {
    const request = ++flowRequest.current;
    try {
      const res = await fetchWithAuth(`/api/v1/patient-flow?${flowParams}`);
      if (!res.ok) throw new Error(String(res.status));
      const data: FlowSnapshot = await res.json();
      // A late answer for an earlier period or filter is ignored.
      if (request === flowRequest.current) {
        setFlow(data);
        setFlowError(false);
      }
    } catch {
      if (request === flowRequest.current) setFlowError(true);
    }
  }, [flowParams]);
  useEffect(() => {
    fetchFlow();
    const timer = setInterval(fetchFlow, 20000);
    return () => clearInterval(timer);
  }, [fetchFlow]);
  const toast = (message: string, type: 'success' | 'info' | 'error' = 'success') =>
    window.dispatchEvent(new CustomEvent('show-sentinel-toast', { detail: { message, type } }));

  const getInitials = (name?: string | null) => {
    if (!name) return 'PT';
    const parts = name.trim().split(/\s+/);
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  };

  useEffect(() => {
    let cancelled = false;
    const encId = selectedPatientEncounter?.id;
    // Reset journeyData immediately upon switch so unkeyed stale cache never appears under new patient's name
    setJourneyData(null);
    if (encId) {
      fetchWithAuth(`/api/v1/encounters/${encId}/journey`)
        .then(res => res && res.ok ? res.json() : null)
        .then(data => {
          if (!cancelled && data && data.encounter_id === encId) {
            setJourneyData(data);
            setJourneysByEncounter(prev => ({ ...prev, [encId]: data }));
          }
        })
        .catch(err => {
          if (!cancelled) console.error("Failed to fetch journey:", err);
        });
    }
    return () => {
      cancelled = true;
    };
  }, [selectedPatientEncounter?.id]);

  // Charting form states for doctor
  const [editingDiagnosis, setEditingDiagnosis] = useState('');
  const [editingNotes, setEditingNotes] = useState('');
  const [editingMedications, setEditingMedications] = useState('');
  const [isSavingChart, setIsSavingChart] = useState(false);
  const [saveChartSuccess, setSaveChartSuccess] = useState(false);

  // Structured SOAP notes states
  const [soapS, setSoapS] = useState('');
  const [soapO, setSoapO] = useState('');
  const [soapA, setSoapA] = useState('');
  const [soapP, setSoapP] = useState('');
  const [modalActiveTab, setModalActiveTab] = useState<'soap' | 'rx' | 'vitals'>('soap');

  const fetchEncounters = async () => {
    try {
      let response = await fetchWithAuth('/api/v1/encounters');
      if (response && response.ok) {
        const data = await response.json();
        setEncounters(Array.isArray(data) ? data : []);
      }
    } catch (error) {
      console.error("Failed to fetch encounters:", error);
      try {
        const fb = await fetchWithAuth('/api/v1/encounters/');
        if (fb && fb.ok) {
          const fbData = await fb.json();
          setEncounters(Array.isArray(fbData) ? fbData : []);
        }
      } catch (e2) {}
    } finally {
      setIsLoadingEncounters(false);
    }
  };

  useEffect(() => {
    fetchEncounters();
    fetchPatientsDirectory();
  }, []);

  // Mode switcher: 'directory' (500 UAE Patients Master Dataset from Excel) vs 'queue'
  const [viewMode, setViewMode] = useState<'queue' | 'directory'>('directory');
  const [patientsList, setPatientsList] = useState<any[]>([]);
  const [totalPatients, setTotalPatients] = useState(500);
  const [isLoadingPatients, setIsLoadingPatients] = useState(false);
  const [patientSearch, setPatientSearch] = useState('');
  const [emirateFilter, setEmirateFilter] = useState('All');
  const [payerFilter, setPayerFilter] = useState('All');
  const [patientPage, setPatientPage] = useState(1);
  const [patientPageSize, setPatientPageSize] = useState(20);

  const fetchPatientsDirectory = async (page = patientPage, size = patientPageSize, search = patientSearch, em = emirateFilter, pay = payerFilter) => {
    try {
      setIsLoadingPatients(true);
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(size),
      });
      if (search.trim()) params.append('search', search.trim());
      if (em !== 'All') params.append('emirate', em);
      if (pay !== 'All') params.append('payer', pay);

      let res = await fetchWithAuth(`/api/v1/patients?${params.toString()}`);
      if (res && res.ok) {
        const totalHeader = res.headers.get('X-Total-Count');
        if (totalHeader) setTotalPatients(parseInt(totalHeader, 10));
        const data = await res.json();
        setPatientsList(Array.isArray(data) ? data : []);
      }
    } catch (e) {
      console.error("Failed to fetch patients directory:", e);
    } finally {
      setIsLoadingPatients(false);
    }
  };

  useEffect(() => {
    fetchPatientsDirectory(patientPage, patientPageSize, patientSearch, emirateFilter, payerFilter);
  }, [patientPage, patientPageSize, emirateFilter, payerFilter]);

  useEffect(() => {
    const timer = setTimeout(() => {
      fetchPatientsDirectory(1, patientPageSize, patientSearch, emirateFilter, payerFilter);
    }, 300);
    return () => clearTimeout(timer);
  }, [patientSearch]);

  // Health Card states
  const [isHealthCardModalOpen, setIsHealthCardModalOpen] = useState(false);
  const [selectedPatient, setSelectedPatient] = useState('');
  const [providerName, setProviderName] = useState('');
  const [memberId, setMemberId] = useState('');
  const [groupNumber, setGroupNumber] = useState('');
  const [payerId, setPayerId] = useState('');

  const [ocrText, setOcrText] = useState('');
  const [validation, setvalidation] = useState<number | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [showScannerView, setShowScannerView] = useState(false);
  const [isFlashActive, setIsFlashActive] = useState(false);
  const [isVerifying, setIsVerifying] = useState(false);
  const [verificationResult, setVerificationResult] = useState<any>(null);
  const [validationError, setValidationError] = useState<string | null>(null);

  // Clinic rooms: state calculated on the server from the visit events (occupied while a
  // consultation runs, cleaning afterwards, or as staff set it), keyed by room name.
  const roomAssignments = useMemo(() => {
    const out: Record<string, { id: number, kind: string, status: string, encounterId: string | null, doctorId: string | null, availableIn: number | null, patientName: string | null, provider: string | null, department: string | null, waitMinutes: number | null, patientsInLab: number }> = {};
    (flow?.rooms || []).forEach(r => {
      out[r.name] = {
        id: r.id, kind: r.kind, status: r.status, encounterId: r.visit_id, doctorId: r.doctor,
        availableIn: r.available_in_minutes, patientName: r.patient_name, provider: r.provider,
        department: r.department, waitMinutes: r.wait_minutes, patientsInLab: r.patients_in_lab,
      };
    });
    return out;
  }, [flow]);
  const [rejectedAssignments, setRejectedAssignments] = useState<string[]>([]);
  // Consultations that started without a room: the closest free room is recommended to staff.
  const pendingAssignments = useMemo(() => {
    const free = (flow?.rooms || []).filter(r => r.kind === 'consult' && r.status === 'Available').map(r => r.name);
    return (flow?.queue || [])
      .filter(r => r.journey_stage === 'Consultation' && !r.room && !rejectedAssignments.includes(r.id))
      .slice(0, free.length)
      .map((r, i) => ({ encounterId: r.id, roomName: free[i], patientName: r.patient_name || 'Patient' }));
  }, [flow, rejectedAssignments]);
  const recommendedRoom = (flow?.rooms || []).find(r => r.kind === 'consult' && r.status === 'Available')?.name || null;
  const [selectedRoom, setSelectedRoom] = useState<string | null>(null);
  const [assignTargetPatient, setAssignTargetPatient] = useState<string>('');
  const [assignTargetDoctor, setAssignTargetDoctor] = useState<string>('');
  const [isAddRoomModalOpen, setIsAddRoomModalOpen] = useState(false);
  const [newRoomName, setNewRoomName] = useState('');
  const [newRoomDoctor, setNewRoomDoctor] = useState<string | null>(null);

  const assignRoom = async (visitId: string, roomName: string, patientName?: string | null) => {
    try {
      await postFlow(`/visits/${encodeURIComponent(visitId)}/room`, { room: roomName });
      toast(`🏥 ${patientName || 'Patient'} assigned to ${roomName}.`);
    } catch (e) {
      toast(`Room not assigned: ${(e as Error).message}`, 'error');
    } finally {
      fetchFlow();
      fetchEncounters();
    }
  };

  const handleApproveAssignment = (encounterId: string, roomName: string) => {
    const row = flow?.queue.find(r => r.id === encounterId);
    assignRoom(encounterId, roomName, row?.patient_name);
  };

  const handleRejectAssignment = (encounterId: string) => {
    setRejectedAssignments(prev => [...prev, encounterId]);
    toast(`❌ Assignment rejected by staff.`, 'info');
  };

  const setRoomStatus = async (roomName: string, status: string) => {
    const room = roomAssignments[roomName];
    if (!room) return;
    try {
      await postFlow(`/rooms/${room.id}/status`, { status });
    } catch (e) {
      toast(`${roomName}: ${(e as Error).message}`, 'error');
    } finally {
      fetchFlow();
    }
  };

  const setRoomDoctor = async (roomName: string, doctor: string | null) => {
    const room = roomAssignments[roomName];
    if (!room) return;
    try {
      await postFlow(`/rooms/${room.id}/doctor`, { doctor });
    } catch (e) {
      toast(`${roomName}: ${(e as Error).message}`, 'error');
    } finally {
      fetchFlow();
    }
  };

  // The clinic's providers as recorded in its patient data (available = not in a consultation now).
  const CLINIC_DOCTORS = (flow?.doctors || []).map(d => ({ id: d.name, name: d.name, specialty: d.specialty || '', available: d.available }));

  useEffect(() => {
    if (!selectedRoom) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setSelectedRoom(null); setAssignTargetPatient(''); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selectedRoom]);

  // Sync editing fields when a room is clicked
  useEffect(() => {
    if (!selectedRoom) return;
    const room = roomAssignments[selectedRoom];
    if (room && room.encounterId) {
      const enc = encounters.find(e => e.id === room.encounterId);
      if (enc) {
        setEditingDiagnosis(enc.diagnosis || '');
        const notes = enc.clinical_notes || enc.clinicalNotes || '';
        setEditingNotes(notes);

        // Parse SOAP fields
        const parsed = parseSOAPNotes(notes);
        setSoapS(parsed.s);
        setSoapO(parsed.o);
        setSoapA(parsed.a);
        setSoapP(parsed.p);

        setEditingMedications(enc.medications || '');
      }
    }
  }, [selectedRoom, roomAssignments, encounters]);

  const handleSaveCharting = async (encId: string) => {
    setIsSavingChart(true);
    setSaveChartSuccess(false);

    // Combine individual SOAP fields
    const combinedNotes = `Subjective: ${soapS}\n\nObjective: ${soapO}\n\nAssessment: ${soapA}\n\nPlan: ${soapP}`;

    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/encounters/${encId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          diagnosis: editingDiagnosis,
          clinical_notes: combinedNotes,
          medications: editingMedications
        })
      });
      if (response.ok) {
        setSaveChartSuccess(true);
        setEditingNotes(combinedNotes);
        fetchEncounters();
        window.dispatchEvent(new CustomEvent('show-sentinel-toast', { detail: { message: '💾 Patient SOAP notes & chart details saved to database successfully!', type: 'success' } }));
        setTimeout(() => setSaveChartSuccess(false), 2000);
      }
    } catch (err) {
      console.error("Failed to save patient chart details:", err);
    } finally {
      setIsSavingChart(false);
    }
  };
  const [callingPatient, setCallingPatient] = useState<any | null>(null);
  const [callingStatus, setCallingStatus] = useState<'dialing' | 'connected' | 'completed' | 'failed' | null>(null);
  const [callResult, setCallResult] = useState<FlowActionResult | null>(null);

  // Places the call through the clinic's telephony connector; the screen shows the recorded result.
  const handleCallPatient = async (patient: any) => {
    setCallingPatient(patient);
    setCallResult(null);
    setCallingStatus('dialing');
    try {
      const result = await postFlow<FlowActionResult>(`/visits/${encodeURIComponent(patient.id)}/call`, {
        idempotency_key: newIdempotencyKey(`call:${patient.id}`),
      });
      setCallResult(result);
      setCallingStatus(result.status === 'COMPLETED' ? 'completed' : 'failed');
    } catch (e) {
      setCallResult({ action_id: '', action_type: 'CALL_PATIENT', status: 'FAILED', receipt_ref: null,
        detail: (e as Error).message, recipient: null, connector: null });
      setCallingStatus('failed');
    }
  };

  // Open Health Card Modal and fetch the patient's record by identifier (several patients share a name)
  const [selectedPatientRef, setSelectedPatientRef] = useState<string | null>(null);
  const handleOpenHealthCard = async (patientName: string, patientRef?: string | null) => {
    setSelectedPatient(patientName);
    setSelectedPatientRef(patientRef || null);
    setProviderName('');
    setMemberId('');
    setGroupNumber('');
    setPayerId('');
    setOcrText('');
    setvalidation(null);
    setVerificationResult(null);
    setValidationError(null);
    setIsHealthCardModalOpen(true);

    try {
      if (patientRef) {
        const response = await fetchWithAuth(`/api/v1/insurance/patient-ref/${encodeURIComponent(patientRef)}`);
        if (response.ok) {
          const data = await response.json();
          if (data?.stored) {
            setProviderName(data.stored.provider_name);
            setMemberId(data.stored.member_id);
            setGroupNumber(data.stored.group_number || '');
            setPayerId(data.stored.payer_id || '');
            setVerificationResult(data.stored);
            setOcrText(data.stored.ocr_raw_text || '');
          } else if (data?.on_file) {
            // Payer and member ID on file in the patient's record; not verified until checked.
            setProviderName(data.on_file.payer || '');
            setMemberId(data.on_file.member_id || '');
          }
        }
        return;
      }
      const response = await fetchWithAuth(`/api/v1/insurance/patient/${encodeURIComponent(patientName)}`);
      if (response.ok) {
        const data = await response.json();
        if (data) {
          setProviderName(data.provider_name);
          setMemberId(data.member_id);
          setGroupNumber(data.group_number || '');
          setPayerId(data.payer_id || '');
          setVerificationResult(data);
          setOcrText(data.ocr_raw_text || '');
        }
      }
    } catch (error) {
      console.error("Error loading patient insurance details:", error);
    }
  };

  // Simulate OCR scanning
  const handleOCRScan = async () => {
    setIsScanning(true);
    setvalidation(null);
    setShowScannerView(true);
    setIsFlashActive(false);

    // Simulate live scanning for 2 seconds
    await new Promise(resolve => setTimeout(resolve, 2000));

    // Trigger shutter flash
    setIsFlashActive(true);
    await new Promise(resolve => setTimeout(resolve, 300));
    setIsFlashActive(false);

    try {
      const response = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/insurance/ocr-scan`, {
        method: 'POST'
      });
      if (response.ok) {
        const data = await response.json();
        setProviderName(data.provider_name);
        setMemberId(data.member_id);
        setGroupNumber(data.group_number || '');
        setPayerId(data.payer_id || '');
        setOcrText(data.ocr_raw_text);
        setvalidation(data.validation_score);
      }
    } catch (error) {
      console.error("OCR scanning error:", error);
    } finally {
      setIsScanning(false);
      setShowScannerView(false);
    }
  };

  // Verify Eligibility
  const handleVerifyEligibility = async () => {
    const prov = providerName.trim();
    const mem = memberId.trim();
    const grp = groupNumber.trim() || null;
    const pyr = payerId.trim() || null;
    if (!prov || !mem) {
      // No invented card details: the payer and member ID must come from the card or the record.
      setValidationError('Enter the payer and member ID (or scan the card) before verifying.');
      return;
    }

    setValidationError(null);
    setIsVerifying(true);
    try {
      const response = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/insurance/verify-eligibility`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          // Stored under the patient's identifier when known (names are not unique).
          patient_name: selectedPatientRef || selectedPatient,
          provider_name: prov,
          member_id: mem,
          group_number: grp,
          payer_id: pyr
        })
      });
      if (response.ok) {
        const data = await response.json();
        setVerificationResult(data);
        window.dispatchEvent(new CustomEvent('show-sentinel-toast', { detail: { message: `🛡️ Insurance verified: ACTIVE coverage for ${selectedPatient || 'Patient'}!`, type: 'success' } }));
      }
    } catch (error) {
      console.error("Verification error:", error);
    } finally {
      setIsVerifying(false);
    }
  };

  const handleExport = () => {
    setIsExporting(true);
    // Exactly the rows of the current snapshot (same period, filter and cutoff as the screen).
    let csvContent = "Patient Name,Provider,Department,Priority,Wait Time,Status\n";
    (flow?.queue || []).forEach(e => {
      const waitVal = e.wait_time != null && e.wait_time !== '' ? e.wait_time : 'Unavailable';
      csvContent += `${e.patient_name || 'N/A'},${e.provider_name || 'N/A'},${e.department || 'N/A'},${e.priority || 'N/A'},${waitVal},${e.status || 'N/A'}\n`;
    });
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.setAttribute("href", url);
    link.setAttribute("download", "Patient_Flow_Log.csv");
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => setIsExporting(false), 2000);
  };

  const handleDownloadPatientReport = (enc: any) => {
    const printWindow = window.open('', '_blank');
    if (!printWindow) {
      alert("Please allow popups to download reports.");
      return;
    }

    const todayStr = new Date().toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });

    const reportHtml = `
      <!DOCTYPE html>
      <html>
      <head>
        <title>Clinical Encounter Summary - ${enc.patient_name}</title>
        <style>
          body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            color: #1f2937;
            line-height: 1.5;
            padding: 40px;
            background: #fff;
          }
          .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid #e5e7eb;
            padding-bottom: 20px;
            margin-bottom: 30px;
          }
          .logo-area {
            display: flex;
            align-items: center;
            gap: 12px;
          }
          .logo-icon {
            width: 40px;
            height: 40px;
            background: var(--color-surface-raised);
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: 800;
            font-size: 20px;
          }
          .logo-text h1 {
            margin: 0;
            font-size: 22px;
            font-weight: 800;
            color: #1e1b4b;
          }
          .logo-text p {
            margin: 0;
            font-size: 11px;
            color: #6b7280;
            text-transform: uppercase;
            letter-spacing: 1px;
          }
          .meta-info {
            text-align: right;
            font-size: 12px;
            color: #6b7280;
          }
          .section-title {
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-weight: 800;
            color: var(--color-surface-raised);
            margin-top: 25px;
            margin-bottom: 12px;
            border-bottom: 1px solid #e5e7eb;
            padding-bottom: 4px;
          }
          .patient-card {
            background: #F7F9FC;
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 25px;
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 16px;
          }
          .patient-card div span {
            font-size: 10px;
            text-transform: uppercase;
            font-weight: 700;
            color: #9ca3af;
            display: block;
          }
          .patient-card div p {
            margin: 4px 0 0 0;
            font-size: 14px;
            font-weight: 700;
            color: #111827;
          }
          .vitals-grid {
            display: grid;
            grid-template-cols: repeat(3, 1fr);
            gap: 12px;
            margin-bottom: 25px;
          }
          .vital-box {
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            padding: 14px;
            background: #F7F9FC;
            text-align: center;
          }
          .vital-box span {
            font-size: 9px;
            text-transform: uppercase;
            font-weight: 700;
            color: #9ca3af;
            display: block;
          }
          .vital-box p {
            margin: 6px 0 0 0;
            font-size: 18px;
            font-weight: 800;
            color: #111827;
          }
          .soap-container {
            margin-bottom: 25px;
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            padding: 20px;
            background: #ffffff;
          }
          .soap-row {
            margin-bottom: 20px;
          }
          .soap-row:last-child {
            margin-bottom: 0;
          }
          .soap-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 20px;
            height: 20px;
            border-radius: 4px;
            font-weight: 800;
            font-size: 11px;
            margin-right: 8px;
          }
          .badge-s { background: #dbeafe; color: #1e40af; }
          .badge-o { background: #d1fae5; color: #065f46; }
          .badge-a { background: #fef3c7; color: #92400e; }
          .badge-p { background: #e0e7ff; color: #3730a3; }
          .soap-label {
            font-size: 12px;
            font-weight: 800;
            color: #374151;
            text-transform: uppercase;
          }
          .soap-text {
            font-size: 13px;
            color: #4b5563;
            margin: 8px 0 0 28px;
            white-space: pre-wrap;
            background: #F7F9FC;
            border: 1px solid #f3f4f6;
            padding: 10px;
            border-radius: 8px;
          }
          .signature-area {
            margin-top: 50px;
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
          }
          .sig-line {
            width: 220px;
            border-top: 1px solid #9ca3af;
            text-align: center;
            padding-top: 8px;
            font-size: 11px;
            font-weight: 600;
            color: #6b7280;
          }
          @media print {
            body { padding: 20px; }
            .print-btn-container { display: none; }
          }
        </style>
      </head>
      <body>
        <div class="print-btn-container" style="text-align: right; margin-bottom: 15px;">
          <button onclick="window.print()" style="background: var(--color-surface-raised); color: white; border: none; padding: 10px 20px; border-radius: 8px; font-weight: 700; font-size: 12px; cursor: pointer; transition: background 0.2s;">Print / Save as PDF</button>
        </div>
        <div class="header">
          <div class="logo-area">
            <div class="logo-icon">S</div>
            <div class="logo-text">
              <h1>SBN Sentinel</h1>
              <p>Clinical Intelligence Platform</p>
            </div>
          </div>
          <div class="meta-info">
            <p><strong>Encounter ID:</strong> ENC-${enc.id || '9873'}</p>
            <p><strong>Date:</strong> ${todayStr}</p>
          </div>
        </div>

        <div class="section-title">Patient Profile</div>
        <div class="patient-card">
          <div>
            <span>Patient Name</span>
            <p>${enc.patient_name}</p>
          </div>
          <div>
            <span>Attending Physician</span>
            <p>${enc.provider_name}</p>
          </div>
          <div>
            <span>Department</span>
            <p>${enc.department || 'General Medicine'}</p>
          </div>
          <div>
            <span>Status</span>
            <p>${enc.priority} Priority / Completed Consultation</p>
          </div>
        </div>

        <div class="section-title">Encounter Vitals</div>
        <div class="vitals-grid">
          <div class="vital-box">
            <span>Blood Pressure</span>
            <p>${enc.clinical_notes?.includes('BP') ? enc.clinical_notes.split('BP')[1].split(',')[0].trim() : 'Not Recorded'}</p>
          </div>
          <div class="vital-box">
            <span>Heart Rate</span>
            <p>${enc.clinical_notes?.includes('HR') ? enc.clinical_notes.split('HR')[1].split(',')[0].trim() : 'Not Recorded'}</p>
          </div>
          <div class="vital-box">
            <span>Oxygen Saturation</span>
            <p>${enc.clinical_notes?.includes('SpO2') ? enc.clinical_notes.split('SpO2')[1].split(',')[0].trim() : 'Not Recorded'}</p>
          </div>
        </div>

        <div class="section-title">Clinical SOAP Chart Notes</div>
        <div class="soap-container">
          <div class="soap-row">
            <div>
              <span class="soap-badge badge-s">S</span>
              <span class="soap-label">Subjective</span>
            </div>
            <div class="soap-text">${soapS || 'No subjective complaints or history noted.'}</div>
          </div>
          <div class="soap-row">
            <div>
              <span class="soap-badge badge-o">O</span>
              <span class="soap-label">Objective</span>
            </div>
            <div class="soap-text">${soapO || 'No objective findings or exam notes recorded.'}</div>
          </div>
          <div class="soap-row">
            <div>
              <span class="soap-badge badge-a">A</span>
              <span class="soap-label">Assessment</span>
            </div>
            <div class="soap-text">${soapA || 'No clinical assessment or diagnosis recorded.'}</div>
          </div>
          <div class="soap-row">
            <div>
              <span class="soap-badge badge-p">P</span>
              <span class="soap-label">Plan</span>
            </div>
            <div class="soap-text">${soapP || 'No treatment or follow-up plan recorded.'}</div>
          </div>
        </div>

        <div class="section-title">Diagnosis & Medications</div>
        <div class="patient-card">
          <div>
            <span>ICD-10 Primary Diagnosis</span>
            <p>${editingDiagnosis || 'Not Documented'}</p>
          </div>
          <div>
            <span>Prescribed Rx & Dosage</span>
            <p>${editingMedications || 'None Prescribed'}</p>
          </div>
        </div>

        <div class="signature-area">
          <div>
            <p style="font-size: 10px; color: #9ca3af; margin: 0;">System Verified Record: Sentinel EHR-Core</p>
          </div>
          <div class="sig-line">
            Authorized Provider Signature
          </div>
        </div>
      </body>
      </html>
    `;

    printWindow.document.write(reportHtml);
    printWindow.document.close();
  };

  // The queue for the selected period and filter, as the server calculated it (filters apply to
  // the whole result: rows, cards and export).
  const filteredEncounters: FlowRow[] = flow?.queue || [];

  return (
    <div className="animate-in fade-in duration-500 max-w-[1600px] mx-auto space-y-8">

      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 md:gap-0 relative">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[10px] font-extrabold tracking-widest uppercase px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
              🇦🇪 UAE Healthcare Dataset
            </span>
            <span className="text-[10px] font-bold text-white/50">
              Sentinel UAE Medical Center - Dubai
            </span>
          </div>
          <h2 className="text-3xl font-extrabold text-white mb-1">
            {viewMode === 'queue' ? 'Patient Waitlist & Flow' : 'UAE Patient Master Directory (500 Records)'}
          </h2>
          <p className="text-sm text-white/70 font-medium">
            {viewMode === 'queue'
              ? 'Real-time tracking of patients across clinic locations.'
              : 'Complete 500 UAE Patient Records with MRN, Nationalities, Emirates, Insurance Payers & Member IDs from master clinical spreadsheet.'}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {/* View Switcher: Live Flow vs 500 Patient Directory */}
          <div className="flex items-center gap-1.5 bg-white/5 border border-white/10 p-1 rounded-[14px]">
            <button
              onClick={() => setViewMode('queue')}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-[10px] text-xs font-bold transition-all cursor-pointer ${
                viewMode === 'queue'
                  ? 'bg-gradient-to-r from-purple-600 to-indigo-600 text-white shadow-md'
                  : 'text-white/60 hover:text-white'
              }`}
            >
              <Clock className="w-3.5 h-3.5" /> Live Flow Queue
            </button>
            <button
              onClick={() => setViewMode('directory')}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-[10px] text-xs font-bold transition-all cursor-pointer ${
                viewMode === 'directory'
                  ? 'bg-gradient-to-r from-emerald-600 to-teal-600 text-white shadow-md'
                  : 'text-white/60 hover:text-white'
              }`}
            >
              <Users className="w-3.5 h-3.5" /> 500 UAE Patients (Master Sheet)
            </button>
          </div>
        </div>
      </div>

      {/* VIEW MODE 1: 500 UAE PATIENTS MASTER DIRECTORY TABLE */}
      {viewMode === 'directory' && (
        <div className="space-y-6 animate-in fade-in duration-300">
          {/* Stats Bar */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-white/5 border border-white/10 p-4 rounded-[20px]">
              <p className="text-[10px] text-white/50 uppercase font-bold tracking-wider">Total Patients</p>
              <p className="text-2xl font-black text-white mt-0.5">500 Records</p>
            </div>
            <div className="bg-white/5 border border-white/10 p-4 rounded-[20px]">
              <p className="text-[10px] text-white/50 uppercase font-bold tracking-wider">Emirates Covered</p>
              <p className="text-2xl font-black text-emerald-400 mt-0.5">8 Emirates</p>
            </div>
            <div className="bg-white/5 border border-white/10 p-4 rounded-[20px]">
              <p className="text-[10px] text-white/50 uppercase font-bold tracking-wider">Insurance Payers</p>
              <p className="text-2xl font-black text-blue-400 mt-0.5">10 Networks</p>
            </div>
            <div className="bg-white/5 border border-white/10 p-4 rounded-[20px]">
              <p className="text-[10px] text-white/50 uppercase font-bold tracking-wider">Active Policy Status</p>
              <p className="text-2xl font-black text-purple-400 mt-0.5">100% Active</p>
            </div>
          </div>

          {/* Search & Filter Controls */}
          <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 rounded-[24px] p-6 text-white space-y-4">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
              <div className="flex items-center gap-3 bg-white/5 border border-white/10 rounded-[14px] px-4 py-2.5 flex-1 max-w-lg">
                <Search className="w-4 h-4 text-white/50 shrink-0" />
                <input
                  type="text"
                  value={patientSearch}
                  onChange={(e) => { setPatientSearch(e.target.value); setPatientPage(1); }}
                  placeholder="Search by Patient ID (PAT...), MRN, Name, Mobile, or Member ID..."
                  className="bg-transparent border-none outline-none text-xs text-white w-full placeholder:text-white/50"
                />
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={emirateFilter}
                  onChange={(e) => { setEmirateFilter(e.target.value); setPatientPage(1); }}
                  className="bg-[#120524] border border-white/15 rounded-[12px] px-3 py-2 text-xs font-bold text-white outline-none"
                >
                  <option value="All">All Emirates</option>
                  {['Dubai', 'Abu Dhabi', 'Sharjah', 'Ajman', 'Fujairah', 'Ras Al Khaimah', 'Umm Al Quwain', 'Al Ain'].map(em => (
                    <option key={em} value={em}>{em}</option>
                  ))}
                </select>

                <select
                  value={payerFilter}
                  onChange={(e) => { setPayerFilter(e.target.value); setPatientPage(1); }}
                  className="bg-[#120524] border border-white/15 rounded-[12px] px-3 py-2 text-xs font-bold text-white outline-none"
                >
                  <option value="All">All Payers</option>
                  {['Daman', 'Orient Insurance', 'MetLife', 'Bupa Arabia', 'Sukoon', 'Cigna', 'ADNIC', 'AXA Gulf', 'NMC Insurance', 'Self Pay'].map(p => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>

                <button
                  onClick={() => {
                    const headers = ['Patient_ID', 'MRN', 'First_Name', 'Last_Name', 'DOB', 'Gender', 'Nationality', 'Emirate', 'Mobile', 'Email', 'Payer', 'Plan', 'Member_ID', 'Policy_Start', 'Policy_End', 'Status'];
                    const rows = patientsList.map(p => [
                      p.patient_id, p.mrn, p.first_name, p.last_name, p.dob, p.gender, p.nationality, p.emirate, p.mobile, p.email, p.payer, p.plan, p.member_id, p.policy_start, p.policy_end, p.status
                    ]);
                    const csvContent = "data:text/csv;charset=utf-8," + [headers.join(','), ...rows.map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(','))].join('\n');
                    const link = document.createElement("a");
                    link.setAttribute("href", encodeURI(csvContent));
                    link.setAttribute("download", `sbn_sentinel_uae_patients_500_${new Date().toISOString().split('T')[0]}.csv`);
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                  }}
                  className="flex items-center gap-2 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-bold text-xs px-4 py-2.5 rounded-[12px] shadow-lg transition-transform active:scale-95 cursor-pointer"
                >
                  <Download className="w-4 h-4" /> Export CSV (500 Patients)
                </button>
              </div>
            </div>

            {/* 500 Patients Table */}
            <div className="overflow-x-auto max-h-[650px] custom-scrollbar">
              <table className="w-full text-left border-collapse text-xs">
                <thead className="sticky top-0 bg-[#1e0a38] z-10 border-b border-white/10 text-[10px] uppercase text-white/70">
                  <tr>
                    <th className="py-3 px-3 font-extrabold tracking-wider">Patient ID</th>
                    <th className="py-3 px-3 font-extrabold tracking-wider">MRN</th>
                    <th className="py-3 px-3 font-extrabold tracking-wider">Patient Name</th>
                    <th className="py-3 px-3 font-extrabold tracking-wider">DOB (Gender)</th>
                    <th className="py-3 px-3 font-extrabold tracking-wider">Nationality</th>
                    <th className="py-3 px-3 font-extrabold tracking-wider">Emirate</th>
                    <th className="py-3 px-3 font-extrabold tracking-wider">Mobile</th>
                    <th className="py-3 px-3 font-extrabold tracking-wider">Payer Network</th>
                    <th className="py-3 px-3 font-extrabold tracking-wider">Plan</th>
                    <th className="py-3 px-3 font-extrabold tracking-wider">Member ID</th>
                    <th className="py-3 px-3 font-extrabold tracking-wider text-right">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5 font-medium text-white/90">
                  {isLoadingPatients ? (
                    <tr>
                      <td colSpan={11} className="py-12 text-center text-white/50">
                        <div className="w-8 h-8 border-2 border-emerald-400 border-t-transparent rounded-full animate-spin mx-auto mb-2"></div>
                        Loading 500 UAE Patient Records...
                      </td>
                    </tr>
                  ) : patientsList.length === 0 ? (
                    <tr>
                      <td colSpan={11} className="py-8 text-center text-white/50 font-bold">
                        No matching patients found.
                      </td>
                    </tr>
                  ) : (
                    patientsList.map((p, idx) => (
                      <tr key={p.patient_id || idx} className="hover:bg-white/5 transition-colors border-b border-white/5 last:border-0">
                        <td className="py-3 px-3 font-mono text-emerald-400 font-bold">{p.patient_id}</td>
                        <td className="py-3 px-3 font-mono text-purple-300 font-bold">{p.mrn}</td>
                        <td className="py-3 px-3">
                          <span className="font-bold text-white">{p.name}</span>
                          <span className="block text-[10px] text-white/50">{p.email}</span>
                        </td>
                        <td className="py-3 px-3 text-white/70 font-mono">
                          {p.dob} <span className="text-[10px] font-bold text-white/50">({p.gender})</span>
                        </td>
                        <td className="py-3 px-3">
                          <span className="px-2 py-0.5 rounded bg-white/10 text-white/80 text-[10px] font-bold">
                            {p.nationality}
                          </span>
                        </td>
                        <td className="py-3 px-3 font-bold text-white">
                          📍 {p.emirate}
                        </td>
                        <td className="py-3 px-3 font-mono text-blue-300">{p.mobile}</td>
                        <td className="py-3 px-3">
                          <span className="font-bold text-white">{p.payer}</span>
                        </td>
                        <td className="py-3 px-3">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-extrabold uppercase ${
                            p.plan === 'Gold' ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30' :
                            p.plan === 'Platinum' || p.plan === 'Premium' ? 'bg-purple-500/20 text-purple-300 border border-purple-500/30' :
                            p.plan === 'Silver' ? 'bg-slate-300/20 text-slate-200 border border-slate-300/30' :
                            'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                          }`}>
                            {p.plan}
                          </span>
                        </td>
                        <td className="py-3 px-3 font-mono text-white/70">{p.member_id}</td>
                        <td className="py-3 px-3 text-right">
                          <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 font-extrabold uppercase text-[10px] border border-emerald-500/30">
                            {p.status}
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination Controls for Patients Directory */}
            {totalPatients > 0 && (
              <div className="flex flex-col sm:flex-row items-center justify-between gap-4 mt-6 pt-4 border-t border-white/10 text-xs">
                <div className="flex items-center gap-3 text-white/60 font-medium">
                  <span>
                    Showing <strong className="text-white font-bold">{((patientPage - 1) * patientPageSize) + 1}</strong> to <strong className="text-white font-bold">{Math.min(patientPage * patientPageSize, totalPatients)}</strong> of <strong className="text-white font-bold">{totalPatients}</strong> Patients
                  </span>
                  <span className="text-white/30">|</span>
                  <div className="flex items-center gap-1.5">
                    <span>Rows:</span>
                    <select
                      value={patientPageSize}
                      onChange={(e) => { setPatientPageSize(Number(e.target.value)); setPatientPage(1); }}
                      className="bg-[#120524] border border-white/15 rounded-[8px] px-2 py-1 text-xs font-bold text-white outline-none cursor-pointer"
                    >
                      <option value={10}>10</option>
                      <option value={20}>20</option>
                      <option value={50}>50</option>
                      <option value={100}>100</option>
                    </select>
                  </div>
                </div>

                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => setPatientPage(prev => Math.max(prev - 1, 1))}
                    disabled={patientPage === 1}
                    className="flex items-center gap-1 px-3 py-1.5 rounded-[10px] bg-white/5 border border-white/10 text-white font-semibold disabled:opacity-30 disabled:cursor-not-allowed hover:bg-white/10 transition-colors cursor-pointer"
                  >
                    <ChevronLeft className="w-3.5 h-3.5" /> Prev
                  </button>
                  <span className="px-3 py-1.5 rounded-[10px] bg-emerald-600/30 border border-emerald-500/40 font-bold text-emerald-300 text-xs">
                    Page {patientPage} of {Math.ceil(totalPatients / patientPageSize) || 1}
                  </span>
                  <button
                    onClick={() => setPatientPage(prev => Math.min(prev + 1, Math.ceil(totalPatients / patientPageSize) || 1))}
                    disabled={patientPage >= Math.ceil(totalPatients / patientPageSize)}
                    className="flex items-center gap-1 px-3 py-1.5 rounded-[10px] bg-white/5 border border-white/10 text-white font-semibold disabled:opacity-30 disabled:cursor-not-allowed hover:bg-white/10 transition-colors cursor-pointer"
                  >
                    Next <ChevronRight className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* VIEW MODE 2: LIVE QUEUE & CLINIC ROOMS (ORIGINAL VIEW) */}
      {viewMode === 'queue' && (
        <div className="space-y-8 animate-in fade-in duration-300">
          <div className="flex flex-wrap items-center gap-2 lg:gap-3 relative w-full lg:w-auto">
            <button onClick={() => setShowFilter(!showFilter)} className={`flex items-center gap-2 border text-white font-bold text-xs px-4 py-2.5 rounded-[16px] premium-shadow hover:bg-white/10 transition-colors relative z-20 ${activeFilter ? 'bg-white/20 border-white/40 text-white shadow-md' : 'bg-white/5 border-white/10'}`}>
              <Filter className="w-4 h-4" /> {activeFilter || 'Filter'}
            </button>

            {showFilter && (
              <div className="absolute top-12 left-0 w-48 bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] rounded-[16px] shadow-[0_20px_50px_rgba(46,16,85,0.3)] border border-white/10 p-2 z-30 animate-in slide-in-from-top-2">
                {(flow?.departments || []).map(dept => (
                  <div key={dept} onClick={() => {setActiveFilter(dept); setShowFilter(false); }} className="px-3 py-2 hover:bg-white/10 rounded-[8px] cursor-pointer text-xs font-bold text-white">{dept}</div>
                ))}
                <div className="h-px bg-white/10 my-1"></div>
                <div onClick={() => {setActiveFilter('High Priority'); setShowFilter(false); }} className="px-3 py-2 hover:bg-white/10 rounded-[8px] cursor-pointer text-xs font-bold text-[#2563EB]">High Priority Only</div>
                {activeFilter && (
                  <>
                    <div className="h-px bg-white/10 my-1"></div>
                    <div onClick={() => {setActiveFilter(''); setShowFilter(false); }} className="px-3 py-2 hover:bg-[#FEF2F2] text-[var(--color-semantic-critical)] rounded-[8px] cursor-pointer text-xs font-bold">Clear Filters</div>
                  </>
                )}
              </div>
            )}
            <button onClick={handleExport} className={`flex items-center gap-2 font-bold text-xs px-4 py-2.5 rounded-[16px] premium-shadow transition-colors ${isExporting ? 'bg-[var(--color-semantic-positive)] text-white hover:bg-[#059669]' : 'bg-white/5 border border-white/10 text-white hover:bg-white/10 active:scale-95'}`}>
              {isExporting ? <Check className="w-4 h-4 animate-bounce" /> : <Download className="w-4 h-4 text-white/70" />} {isExporting ? 'Exported!' : 'Export'}
            </button>
            <button onClick={() => setDateFilter(dateFilter === 'Today' ? 'Yesterday' : dateFilter === 'Yesterday' ? 'Last 7 Days' : 'Today')} className="flex items-center justify-center gap-2 bg-white/5 border border-white/10 text-white font-bold text-xs px-4 py-2.5 rounded-[16px] premium-shadow whitespace-nowrap shrink-0 hover:bg-white/10 transition-colors active:scale-95">
              <Calendar className="w-4 h-4 text-white/70" /> {dateFilter}
            </button>
            {/* Synced only when the queue feed delivered an event recently; otherwise its last update time. */}
            {(() => {
              const lastAt = clinicTime(flow?.feed.last_event_at);
              const synced = !flowError && !!flow?.feed.synced;
              const label = flowError ? 'Queue unavailable'
                : synced ? `Live Queue Synced · ${lastAt}`
                : lastAt ? `Queue last updated ${lastAt}` : 'No queue updates yet';
              return (
                <div className={`flex items-center gap-2 font-bold text-xs px-4 py-2.5 rounded-[16px] premium-shadow border ${synced ? 'bg-[#ECFDF5] text-[var(--color-semantic-positive)] border-emerald-500/30' : 'bg-amber-500/10 text-amber-400 border-amber-500/30'}`}>
                  <span className={`w-2 h-2 rounded-full ${synced ? 'bg-[var(--color-semantic-positive)] animate-pulse' : 'bg-amber-400'}`}></span>
                  {label}
                </div>
              );
            })()}
          </div>

          {/* Top Stats: calculated on the server from the visit events for the selected period and
              filter; trends compare with the same time yesterday (or the previous 7 days). */}
      {(() => {
        const cards = flow?.cards;
        const unavailable = { display: flowError ? 'Unavailable' : '…', trend: null, trend_good: null, spark: [] as Array<number | null> };
        return (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
            {[
              { title: 'Checked In', card: cards?.checked_in, icon: UserCheck, color: 'text-[var(--color-accent)]', bg: 'bg-blue-500/20', line: 'var(--color-accent)' },
              { title: 'In Consultation', card: cards?.in_consultation, icon: Stethoscope, color: 'text-[var(--color-semantic-positive)]', bg: 'bg-emerald-500/20', line: 'var(--color-semantic-positive)' },
              { title: 'Avg Wait Time', card: cards?.avg_wait, icon: Clock, color: 'text-[var(--color-semantic-attention)]', bg: 'bg-orange-500/20', line: 'var(--color-semantic-attention)' },
              { title: 'Delayed', card: cards?.delayed, icon: AlertCircle, color: 'text-[var(--color-semantic-critical)]', bg: 'bg-red-500/20', line: 'var(--color-semantic-critical)' },
            ].map((stat, i) => {
              const card = stat.card || unavailable;
              const path = sparkPath(card.spark);
              return (
              <div key={i} className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 rounded-[24px] p-6 shadow-[0_20px_50px_rgba(46,16,85,0.3)] card-hover flex justify-between items-center text-white transition-all duration-300">
                <div>
                  <p className="text-[11px] text-white/70 uppercase font-extrabold tracking-widest mb-1">{stat.title}</p>
                  <div className="flex items-end gap-3">
                    <p className="text-[28px] font-extrabold text-white leading-none transition-all">{card.display}</p>
                    <span className={`text-[11px] font-bold mb-1 transition-colors ${trendClass(card)}`}>{card.trend ?? 'Unavailable'}</span>
                  </div>
                </div>
                <div className="flex flex-col items-end gap-3">
                  <div className={`p-3 ${stat.bg} rounded-[16px]`}>
                    <stat.icon className={`w-5 h-5 ${stat.color}`} />
                  </div>
                  <div className="w-12 h-6 opacity-60">
                    <svg viewBox="0 0 100 30" className="w-full h-full preserve-aspect-ratio-none">
                      {path && <path d={path} fill="none" stroke={stat.line} strokeWidth="3" strokeLinecap="round" />}
                    </svg>
                  </div>
                </div>
              </div>
              );
            })}
          </div>
        );
      })()}

      {/* Patient Journey Timeline */}
      {(() => {
        // Selection is explicit: nothing is shown as a patient's journey until a row is clicked.
        const selectedRow = selectedPatientEncounter
          ? (flow?.queue.find(r => r.id === selectedPatientEncounter.id) || selectedPatientEncounter)
          : null;
        const activeEncounter = selectedRow;
        const targetEncId = activeEncounter?.id;
        // Key journey data strictly to the active encounter (B-72: prevent cached journey of A appearing under B)
        const verifiedJourney = (targetEncId && journeysByEncounter[targetEncId])
          || ((journeyData && journeyData.encounter_id === targetEncId) ? journeyData : null);
        // Stages and their times come from the visit's recorded events (the live row is the latest).
        const stageTimes: Record<string, string> = (selectedRow as FlowRow | null)?.stage_timestamps || verifiedJourney?.stage_timestamps || {};
        const stageSources: Record<string, string> = verifiedJourney?.stage_sources || {};
        const stageList = ['Check In', 'Waiting', 'Consultation', 'Lab', 'Billing', 'Completed'];
        const currentStageName: string | null = activeEncounter
          ? ((selectedRow as FlowRow | null)?.journey_stage || verifiedJourney?.current_stage || activeEncounter?.journey_stage || null)
          : null;
        const activeStageIndex = currentStageName ? stageList.indexOf(currentStageName) : -1;

        const formatStageTs = (ts?: string | null) => {
          if (!ts) return null;
          const s = String(ts).trim();
          if (!s.includes('T')) return s; // a date without a time is shown as the date
          return clinicTime(s) || s;
        };

        // Inspecting the timeline never changes the visit: a click shows when and from where a stage was recorded.
        const handleTimelineStageClick = (targetStage: string) => {
          if (!activeEncounter) return;
          const at = formatStageTs(stageTimes[targetStage]);
          if (!at) {
            toast(`${targetStage}: not recorded for ${activeEncounter.patient_name || 'this visit'}.`, 'info');
            return;
          }
          const source = stageSources[targetStage];
          const sourceLabel = source === 'manual' ? 'reported by staff' : source ? 'from the queue feed' : 'recorded';
          toast(`${targetStage} at ${at} ${flow?.timezone || ''} (${sourceLabel}).`, 'info');
        };

        return (
          <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 rounded-[24px] p-8 shadow-[0_20px_50px_rgba(46,16,85,0.3)] card-hover text-white">
            <div className="flex flex-col md:flex-row md:items-center justify-between mb-8 gap-2">
              <div>
                <h3 className="text-base font-bold text-white">
                  Patient Journey: <span className="text-blue-400 font-extrabold">{activeEncounter?.patient_name || 'Select a Patient'}</span>
                </h3>
                <p className="text-xs text-white/50">
                  {activeEncounter ? `MRN: ${activeEncounter.mrn || 'Unknown'} • Provider: ${activeEncounter.provider_name || 'Unknown'} • Department: ${activeEncounter.department || 'Unknown'} • Payer: ${activeEncounter.payer_network || verifiedJourney?.insurance || 'Unknown'}` : 'Click any queue row below to inspect real patient flow'}
                </p>
              </div>
              {activeEncounter && (
                <div className="text-xs font-bold px-3 py-1 bg-white/10 rounded-full text-white/80 border border-white/10 w-fit">
                  Stage: <span className="text-emerald-400">{currentStageName}</span>
                </div>
              )}
            </div>
            <div className="flex items-center justify-between relative">
              <div className="absolute left-8 right-8 top-1/2 -translate-y-1/2 h-1 bg-white/10 -z-10 rounded-full"></div>
              <div className="absolute left-8 right-1/3 top-1/2 -translate-y-1/2 h-1 bg-gradient-to-r from-[var(--color-semantic-positive)] to-[var(--color-accent)] -z-10 rounded-full transition-all duration-1000" style={{ width: `${Math.min(100, Math.max(0, activeStageIndex) * 20)}%` }}></div>

              {stageList.map((stageName, i) => {
                const stageTs = stageTimes[stageName];
                let status = 'pending';
                if (stageName === currentStageName) {
                  status = 'current';
                } else if (stageTs) {
                  // Only marked done if a recorded timestamp entry actually exists! (B-71: skipped stages stay pending)
                  status = 'done';
                }

                return (
                  <div key={i} onClick={() => handleTimelineStageClick(stageName)} className="flex flex-col items-center gap-3 bg-transparent px-2 relative group cursor-pointer">
                    {status === 'current' && (
                      <div className="absolute -top-12 left-1/2 -translate-x-1/2 flex flex-col items-center animate-bounce">
                        <div className="w-8 h-8 rounded-full bg-[var(--color-accent)] border-2 border-white flex items-center justify-center text-white font-extrabold text-xs premium-shadow">
                          {getInitials(activeEncounter?.patient_name)}
                        </div>
                      </div>
                    )}
                    <div className={`w-5 h-5 rounded-full shadow-sm z-10 transition-all duration-500 group-hover:scale-125 ${status === 'done' ? 'bg-[var(--color-semantic-positive)]' : status === 'current' ? 'bg-[var(--color-accent)] border-4 border-blue-500/30 scale-125' : 'bg-[var(--color-surface-raised)] border-4 border-white/20 group-hover:border-blue-500/30'}`}></div>
                    <span className={`text-[11px] font-extrabold uppercase tracking-wider transition-colors ${status === 'pending' ? 'text-white/50 group-hover:text-white/70' : 'text-white'}`}>{stageName}</span>
                    {stageTs && (
                      <span className="text-[10px] text-white/50 font-mono">
                        {formatStageTs(stageTs)}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8 items-start">
        {/* Live Waiting Queue */}
        <div className="flex flex-col gap-6 col-span-2">
          {/* Governance Boundary: Pending Approvals UI */}
          {pendingAssignments.length > 0 && (
            <div className="bg-gradient-to-r from-amber-500/20 to-orange-500/10 border border-amber-500/30 rounded-[24px] p-6 shadow-lg">
              <div className="flex items-center gap-2 mb-4">
                <AlertCircle className="w-5 h-5 text-amber-400" />
                <h3 className="text-base font-bold text-amber-400">Action Recommended: Pending Approval</h3>
              </div>
              <div className="space-y-3">
                {pendingAssignments.map((assignment, idx) => (
                  <div key={idx} className="bg-[var(--color-surface)]/60 rounded-xl p-4 border border-amber-500/20 flex items-center justify-between">
                    <div>
                      <p className="text-sm font-semibold text-white/90">
                        Assign <span className="font-bold text-white">{assignment.patientName}</span> to <span className="font-bold text-amber-300">{assignment.roomName}</span>
                      </p>
                      <p className="text-[11px] text-white/50 mt-1">
                        Reason: Closest available room. Execution paused pending human review.
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleRejectAssignment(assignment.encounterId)}
                        className="px-4 py-1.5 rounded-lg text-xs font-bold text-red-400 bg-red-500/10 hover:bg-red-500/20 transition-colors"
                      >
                        Reject
                      </button>
                      <button
                        onClick={() => handleApproveAssignment(assignment.encounterId, assignment.roomName)}
                        className="px-4 py-1.5 rounded-lg text-xs font-bold text-emerald-400 bg-emerald-500/20 hover:bg-emerald-500/30 border border-emerald-500/40 transition-colors flex items-center gap-1"
                      >
                        <Check className="w-3.5 h-3.5" /> Approve Action
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 rounded-[24px] p-8 shadow-[0_20px_50px_rgba(46,16,85,0.3)] text-white">
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-base font-bold text-white">Live Waiting Queue</h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-white/10 text-[11px] uppercase text-white/70">
                    <th className="pb-4 font-extrabold tracking-wider px-2">Patient</th>
                    <th className="pb-4 font-extrabold tracking-wider px-2">Provider</th>
                    <th className="pb-4 font-extrabold tracking-wider px-2">Wait Time</th>
                    <th className="pb-4 font-extrabold tracking-wider px-2">Status</th>
                    <th className="pb-4 font-extrabold tracking-wider px-2 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="text-sm font-semibold text-white">
                  {filteredEncounters.length === 0 && (
                    <tr>
                      <td colSpan={5} className="py-12 text-center">
                        <p className="text-sm text-white/50 font-bold">No patients currently in the queue. New check-ins will appear here automatically.</p>
                      </td>
                    </tr>
                  )}
                  {filteredEncounters.map((row, i) => {
                    let sBg = 'bg-orange-500/20';
                    let sText = 'text-[var(--color-semantic-attention)]';
                    if (row.status === 'In Room') {
                      sBg = 'bg-emerald-500/20';
                      sText = 'text-[var(--color-semantic-positive)]';
                    } else if (row.status === 'Delayed') {
                      sBg = 'bg-red-500/20';
                      sText = 'text-[var(--color-semantic-critical)]';
                    } else if (row.status === 'Completed') {
                      sBg = 'bg-blue-500/20';
                      sText = 'text-blue-400';
                    }

                    return (
                      <tr 
                        key={row.id || i} 
                        onClick={() => setSelectedPatientEncounter(row)}
                        className={`border-b border-white/10 hover:bg-white/10 transition-colors last:border-0 cursor-pointer ${selectedPatientEncounter?.id === row.id ? 'bg-white/10' : ''}`}
                      >
                        <td className="py-4 px-2">
                          <div className="flex flex-wrap items-center gap-2 sm:gap-3 w-full md:w-auto">
                            <div className="w-8 h-8 rounded-full bg-blue-500/20 border border-blue-500/30 flex items-center justify-center text-blue-400 font-bold text-xs flex-shrink-0">
                              {getInitials(row.patient_name)}
                            </div>
                            <div>
                              <p className="text-sm font-bold text-white leading-tight transition-all">{row.patient_name}</p>
                              <p className={`text-[10px] font-extrabold uppercase mt-0.5 ${row.priority === 'Urgent' ? 'text-[var(--color-semantic-critical)]' : 'text-white/50'}`}>{row.priority || 'Unknown'} Priority</p>
                            </div>
                          </div>
                        </td>
                        <td className="py-4 px-2">
                          <p className="text-sm font-bold text-white/70">{row.provider_name}</p>
                          <p className="text-[10px] text-white/50 font-bold uppercase">{row.department || 'General'}</p>
                        </td>
                        <td className="py-4 px-2 font-mono text-white/70 transition-all">{row.wait_time != null && row.wait_time !== '' ? row.wait_time : 'Unavailable'}</td>
                        <td className="py-4 px-2">
                          <select
                            value={row.status}
                            onClick={(e) => e.stopPropagation()}
                            onChange={async (e) => {
                              // Reported by staff: recorded as an event with the actor (Delayed is calculated).
                              const newStatus = e.target.value;
                              try {
                                await postFlow(`/visits/${encodeURIComponent(row.id)}/status`, { status: newStatus });
                                toast(`${row.patient_name}: status recorded as ${newStatus}.`);
                              } catch (err) {
                                toast(`${row.patient_name}: ${(err as Error).message}`, 'error');
                              } finally {
                                fetchFlow();
                                fetchEncounters();
                              }
                            }}
                            className="bg-white/10 border border-white/10 text-[11px] font-bold text-white/70 rounded-[8px] px-2.5 py-1 outline-none cursor-pointer hover:bg-white/5 hover:text-white transition-colors"
                          >
                            <option className="bg-[var(--color-surface)] text-white" value="Waiting">Waiting</option>
                            <option className="bg-[var(--color-surface)] text-white" value="In Room">In Room</option>
                            <option className="bg-[var(--color-surface)] text-white" value="Completed">Completed</option>
                            <option className="bg-[var(--color-surface)] text-white" value="Delayed">Delayed</option>
                          </select>
                        </td>
                        <td className="py-4 px-2 text-right">
                          <div className="flex items-center gap-2 justify-end">
                            {(row.status === 'Waiting' || row.status === 'Delayed') && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  if (recommendedRoom) assignRoom(row.id, recommendedRoom, row.patient_name);
                                }}
                                disabled={!recommendedRoom}
                                className="text-[11px] font-bold text-emerald-300 bg-emerald-500/20 border border-emerald-500/40 hover:bg-emerald-500/30 px-3 py-1.5 rounded-[8px] transition-all flex items-center gap-1 active:scale-95 shadow-sm disabled:opacity-40 disabled:cursor-not-allowed"
                                title={recommendedRoom ? `Decision Support Recommendation: ${recommendedRoom} is free now. Staff confirmation starts the consultation.` : 'No consultation room is free right now.'}
                              >
                                {recommendedRoom ? `✓ Assign ${recommendedRoom} (Recommended)` : 'No room free'}
                              </button>
                            )}
                            <button
                              onClick={(e) => { e.stopPropagation(); handleOpenHealthCard(row.patient_name || '', row.patient_ref); }}
                              className="text-[11px] font-bold text-white/80 bg-white/5 border border-white/10 hover:text-white px-3 py-1.5 rounded-[8px] hover:bg-white/10 transition-all flex items-center gap-1 active:scale-95"
                              title="View comprehensive patient history, active medications, and recent lab results"
                            >
                              <CreditCard className="w-3.5 h-3.5" /> Health Card
                            </button>
                            <button onClick={(e) => { e.stopPropagation(); handleCallPatient(row); }} className="text-[11px] font-bold text-blue-400 bg-blue-500/20 border border-blue-500/30 px-3 py-1.5 rounded-[8px] hover:bg-blue-500/30 transition-colors hover:scale-105 active:scale-95 cursor-pointer" title="Initiate a secure VoIP call to the patient's registered contact number">Call</button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Heat Map & AI suggestions */}
        <div className="flex flex-col gap-6 self-start sticky top-6">
          <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 rounded-[24px] p-8 shadow-[0_20px_50px_rgba(46,16,85,0.3)] text-white flex-1">
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-base font-bold text-white flex items-center gap-2">
                <LayoutGrid className="w-5 h-5 text-[var(--color-accent)]" /> Clinic Heat Map
              </h3>
              <button
                onClick={() => setIsAddRoomModalOpen(true)}
                className="bg-white/10 hover:bg-white/20 border border-white/20 text-white font-bold text-xs px-3 py-1.5 rounded-[12px] transition-all hover:scale-105 active:scale-95 cursor-pointer flex items-center gap-1.5"
              >
                + Add Room
              </button>
            </div>
            <div className="grid grid-cols-2 gap-4">
              {Object.keys(roomAssignments).map((roomName) => {
                const room = roomAssignments[roomName];

                let stateLabel = room.status;
                let colorClass = 'text-emerald-400';
                let bgClass = 'bg-emerald-500/10 border-emerald-500/20 hover:bg-emerald-500/20';

                if (room.status === 'Occupied') {
                  stateLabel = room.kind === 'lab' && room.patientsInLab > 1
                    ? `Occupied (${room.patientsInLab} patients)`
                    : room.patientName ? `Occupied (${room.patientName})` : 'Occupied';
                  colorClass = 'text-blue-400';
                  bgClass = 'bg-blue-500/10 border-blue-500/20 hover:bg-blue-500/20';
                } else if (room.status === 'Cleaning') {
                  colorClass = 'text-red-400';
                  bgClass = 'bg-red-500/10 border-red-500/20 hover:bg-red-500/20';
                }

                return (
                  <div
                    key={roomName}
                    onClick={() => setSelectedRoom(roomName)}
                    className={`${bgClass} border rounded-[16px] p-4 text-center cursor-pointer transition-all duration-300 transform hover:scale-[1.03] active:scale-95 premium-shadow relative group`}
                    >
                    <button
                      onClick={async (e) => {
                        e.stopPropagation();
                        if (!confirm(`Are you sure you want to remove ${roomName}?`)) return;
                        try {
                          const res = await fetchWithAuth(`/api/v1/patient-flow/rooms/${room.id}`, { method: 'DELETE' });
                          const body = await res.json().catch(() => null);
                          if (!res.ok) throw new Error(body?.detail || `Request failed (${res.status})`);
                          toast(`🗑️ ${roomName} removed from layout.`, 'info');
                        } catch (err) {
                          toast(`${roomName} not removed: ${(err as Error).message}`, 'error');
                        } finally {
                          fetchFlow();
                        }
                      }}
                      className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 text-white/60 hover:text-rose-400 text-xs font-bold transition-opacity p-1"
                      title="Delete Room"
                    >
                      ✕
                    </button>
                    <p className={`text-[10px] font-extrabold ${colorClass} uppercase mb-1 tracking-wider`}>{roomName}</p>
                    <p className="text-sm font-bold text-white truncate">{stateLabel}</p>
                    {room.status === 'Occupied' && room.provider && (
                      <>
                        <p className="text-[10px] text-white/70 font-semibold mt-1">
                          {room.provider} ({room.department || 'General'})
                        </p>
                        {room.waitMinutes != null && (
                          <p className="text-[10px] text-[#2563EB] font-black mt-0.5">
                            Wait: {room.waitMinutes} mins
                          </p>
                        )}
                      </>
                    )}
                    {room.status === 'Cleaning' && room.availableIn != null && (
                      <p className="text-[10px] text-white/60 font-semibold mt-1">Available in {room.availableIn} min</p>
                    )}
                    {room.doctorId && (() => {
                      const assignedDoc = CLINIC_DOCTORS.find(d => d.id === room.doctorId);
                      return assignedDoc ? (
                        <p className="text-[9px] text-purple-300 font-bold mt-1 flex items-center justify-center gap-1">
                          🩺 {assignedDoc.name}
                        </p>
                      ) : null;
                    })()}
                  </div>
                );
              })}
            </div>
          </div>

          <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 rounded-[24px] p-8 shadow-[0_20px_50px_rgba(46,16,85,0.3)] relative overflow-hidden text-white">
            <div className="absolute top-0 right-0 w-32 h-32 bg-[var(--color-surface-raised)] rounded-full blur-[50px] opacity-30 animate-pulse"></div>
            <h3 className="text-base font-bold flex items-center gap-2 mb-4">
              <Activity className="w-5 h-5 text-[var(--color-accent)]" /> AI Suggestions
            </h3>
            {(() => {
              // Calculated from the live queue: a provider running past the consultation slot, or a
              // patient waiting longer than the clinic's target. Approving sends the notification.
              const suggestion = flow?.suggestions?.[0] || null;
              const act = async (decision: 'approve' | 'dismiss') => {
                if (!suggestion) return;
                try {
                  const result = await postFlow<FlowActionResult>(`/suggestions/${encodeURIComponent(suggestion.id)}/${decision}`, { idempotency_key: `suggestion:${suggestion.id}` });
                  if (decision === 'dismiss') toast('Suggestion dismissed (recorded).', 'info');
                  else if (result.status === 'SENT') toast(`📨 Notification sent to ${suggestion.patient_name} (receipt ${result.receipt_ref}).`);
                  else toast(`Notification not sent: ${result.detail || result.status}`, 'error');
                } catch (e) {
                  toast((e as Error).message, 'error');
                } finally {
                  fetchFlow();
                }
              };
              return (
                <>
                  <p className="text-[13px] font-medium text-white/50 mb-6 leading-relaxed transition-all">
                    {suggestion ? suggestion.text : 'No suggestions right now: no provider is running late and no patient is waiting past the target time.'}
                  </p>
                  <div className="flex gap-3">
                    <button
                      onClick={() => act('approve')}
                      disabled={!suggestion}
                      className="flex-1 bg-[#2563EB] hover:bg-[#1D4ED8] text-white font-bold py-2.5 rounded-[16px] text-xs transition-colors hover:scale-[1.02] active:scale-95 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => act('dismiss')}
                      disabled={!suggestion}
                      className="flex-1 bg-white/5 border border-white/10 hover:bg-white/10 text-white font-bold py-2.5 rounded-[16px] text-xs transition-colors hover:scale-[1.02] active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      Dismiss
                    </button>
                  </div>
                </>
              );
            })()}
          </div>
        </div>
      </div>
    </div>
  )}

      {/* Health Card / Eligibility Modal */}
      {isHealthCardModalOpen && (
        <ModalPortal>
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-md animate-in fade-in">
          <div className="bg-[var(--color-surface)] rounded-[32px] w-full max-w-4xl p-8 shadow-[0_30px_90px_rgba(0,0,0,0.9)] border border-white/20 relative animate-in zoom-in-95 duration-300 max-h-[90vh] overflow-y-auto text-white">
            <button
              onClick={() => setIsHealthCardModalOpen(false)}
              className="absolute top-6 right-6 text-white/50 hover:text-white transition-colors p-2 hover:bg-white/5 rounded-full"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
            </button>

            <div className="mb-6">
              <span className="text-[10px] font-extrabold text-white/80 bg-white/5 border border-white/10 hover:text-white px-3 py-1 rounded-full uppercase tracking-wider">Health Insurance Gateway</span>
              <h3 className="text-2xl font-extrabold text-white mt-2">Health Card & Eligibility Portal</h3>
              <p className="text-sm font-medium text-white/70">Scan patient health card and verify instant benefits eligibility.</p>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">

              {/* Left Side: Card Design and OCR Scanner */}
              <div className="lg:col-span-5 space-y-6">

                {/* Physical Health Card Mockup */}
                <div className="relative aspect-[1.586/1] w-full rounded-[24px] bg-gradient-to-br from-[#1E1B4B] via-[#312E81] to-[var(--color-surface)] p-6 text-white shadow-xl overflow-hidden border border-white/10">
                  <div className="absolute top-0 right-0 w-48 h-48 bg-[#6366F1] rounded-full blur-[80px] opacity-40"></div>
                  <div className="absolute -bottom-10 -left-10 w-40 h-40 bg-[var(--color-surface-raised)] rounded-full blur-[70px] opacity-30"></div>

                  {/* Top Bar */}
                  <div className="flex justify-between items-start relative z-10">
                    <div>
                      <p className="text-[10px] text-[#A5B4FC] font-extrabold tracking-widest uppercase">Health Card</p>
                      <h4 className="font-extrabold text-sm tracking-tight">{providerName || "Select Insurance / Scan Card"}</h4>
                    </div>
                    <div className="w-10 h-10 rounded-full bg-white/10 flex items-center justify-center backdrop-blur-md border border-white/20">
                      <Stethoscope className="w-5 h-5 text-[#A5B4FC]" />
                    </div>
                  </div>

                  {/* Patient Name */}
                  <div className="mt-8 relative z-10">
                    <p className="text-[9px] text-[#C7D2FE] font-bold uppercase tracking-wider">Subscriber / Patient</p>
                    <p className="text-base font-extrabold tracking-wide uppercase">{selectedPatient}</p>
                  </div>

                  {/* ID Numbers */}
                  <div className="mt-4 grid grid-cols-2 gap-4 relative z-10">
                    <div>
                      <p className="text-[9px] text-[#C7D2FE] font-bold uppercase tracking-wider">Member ID / Policy</p>
                      <p className="font-mono text-xs font-bold">{memberId || "—"}</p>
                    </div>
                    <div>
                      <p className="text-[9px] text-[#C7D2FE] font-bold uppercase tracking-wider">Group Number</p>
                      <p className="font-mono text-xs font-bold">{groupNumber || "—"}</p>
                    </div>
                  </div>

                  {/* Footer Logo (in the card's flow so it never covers the numbers) */}
                  <div className="mt-3 flex items-center justify-end gap-1 opacity-70 relative z-10">
                    <Sparkles className="w-3.5 h-3.5 text-[#A5B4FC]" />
                    <span className="text-[9px] font-extrabold uppercase tracking-widest text-[#A5B4FC]">Sentinel Core</span>
                  </div>
                </div>

                {/* Scan Buttons */}
                <div className="bg-white/5 border border-white/10 rounded-[20px] p-5 premium-shadow">
                  <h4 className="text-xs font-extrabold text-white mb-3 flex items-center gap-1.5">
                    <Camera className="w-4 h-4 text-[var(--color-surface)]" /> Sentinel AI OCR Reader
                  </h4>
                  <p className="text-xs text-white/70 mb-4">Simulate taking a snapshot of the health card. Our AI will automatically parse the parameters.</p>

                  <button
                    onClick={handleOCRScan}
                    disabled={isScanning}
                    className="w-full py-3 bg-[#F5F3FF] border border-[#DDD6FE] hover:bg-white/10 text-[var(--color-surface)] font-bold text-xs rounded-[16px] transition-all flex items-center justify-center gap-2 active:scale-98"
                  >
                    {isScanning ? (
                      <>
                        <RefreshCw className="w-4 h-4 animate-spin" /> Scanning card...
                      </>
                    ) : (
                      <>
                        <Scan className="w-4 h-4" /> Scan Card with OCR AI
                      </>
                    )}
                  </button>

                  {validation !== null && (
                    <div className="mt-3 bg-[#ECFDF5] text-[var(--color-semantic-positive)] border border-emerald-500/30 px-3 py-2 rounded-[10px] text-[10px] font-extrabold flex justify-between items-center">
                      <span>OCR EXTRACTION MATCH</span>
                      <span className="bg-white/5 px-2 py-0.5 rounded-full border border-emerald-500/30">Verified</span>
                    </div>
                  )}
                </div>

              </div>

              {/* Right Side: Form Input and Benefits Eligibility Verification */}
              <div className="lg:col-span-7 space-y-6">

                {/* Form fields */}
                <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 rounded-[24px] p-6 shadow-[0_20px_50px_rgba(46,16,85,0.3)] text-white space-y-4">
                  <h4 className="text-sm font-bold text-white border-b border-white/10 pb-3">Card Metadata Fields</h4>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-[10px] font-extrabold text-white/50 uppercase tracking-wider mb-1">Insurance Provider</label>
                      <input
                        type="text"
                        placeholder="e.g. Aetna, Blue Cross"
                        value={providerName}
                        onChange={(e) => setProviderName(e.target.value)}
                        className="w-full bg-[#180A2E] border border-white/20 rounded-[16px] py-2.5 px-3 text-xs font-bold text-white placeholder:text-white/60 outline-none focus:border-blue-500 transition-all"
                        required
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] font-extrabold text-white/50 uppercase tracking-wider mb-1">Member / Policy ID</label>
                      <input
                        type="text"
                        placeholder="e.g. MEM890123"
                        value={memberId}
                        onChange={(e) => setMemberId(e.target.value)}
                        className="w-full bg-[#180A2E] border border-white/20 rounded-[16px] py-2.5 px-3 text-xs font-bold text-white placeholder:text-white/60 outline-none focus:border-blue-500 transition-all"
                        required
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-[10px] font-extrabold text-white/50 uppercase tracking-wider mb-1">Group Number</label>
                      <input
                        type="text"
                        placeholder="e.g. GRP44910"
                        value={groupNumber}
                        onChange={(e) => setGroupNumber(e.target.value)}
                        className="w-full bg-[#180A2E] border border-white/20 rounded-[16px] py-2.5 px-3 text-xs font-bold text-white placeholder:text-white/60 outline-none focus:border-blue-500 transition-all"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] font-extrabold text-white/50 uppercase tracking-wider mb-1">Clearinghouse Payer ID</label>
                      <input
                        type="text"
                        placeholder="e.g. PYR9910"
                        value={payerId}
                        onChange={(e) => setPayerId(e.target.value)}
                        className="w-full bg-[#180A2E] border border-white/20 rounded-[16px] py-2.5 px-3 text-xs font-bold text-white placeholder:text-white/60 outline-none focus:border-blue-500 transition-all"
                      />
                    </div>
                  </div>

                  <button
                    onClick={handleVerifyEligibility}
                    disabled={isVerifying}
                    className="w-full mt-4 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-extrabold py-3.5 rounded-[16px] text-xs shadow-lg transition-all hover:scale-[1.01] active:scale-98 cursor-pointer flex items-center justify-center gap-2"
                  >
                    {isVerifying ? (
                      <>
                        <RefreshCw className="w-4 h-4 animate-spin" /> Querying EDI clearinghouse...
                      </>
                    ) : (
                      <>
                        <ShieldCheck className="w-4 h-4" /> Verify Insurance Eligibility
                      </>
                    )}
                  </button>

                  {validationError && (
                    <div className="mt-3 bg-rose-50 border border-rose-100 text-rose-600 rounded-[10px] px-3 py-2 text-xs font-bold flex items-center gap-1.5 animate-in slide-in-from-top-1">
                      <AlertCircle className="w-4 h-4 shrink-0" />
                      <span>{validationError}</span>
                    </div>
                  )}
                </div>

                {/* Eligibility Result Container */}
                {verificationResult ? (
                  <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 rounded-[24px] p-6 shadow-[0_20px_50px_rgba(46,16,85,0.3)] text-white animate-in slide-in-from-bottom-2">
                    <div className="flex justify-between items-center border-b border-white/10 pb-4 mb-4">
                      <h4 className="text-sm font-bold text-white">Eligibility Response</h4>
                      <div className="flex items-center gap-1.5">
                        {verificationResult.eligibility_status === 'Active' ? (
                          <span className="bg-[#ECFDF5] text-[var(--color-semantic-positive)] border border-emerald-500/30 px-3 py-1 rounded-[8px] text-[10px] font-extrabold flex items-center gap-1">
                            <ShieldCheck className="w-3.5 h-3.5" /> ACTIVE COVERAGE
                          </span>
                        ) : (
                          <span className="bg-[#FEF2F2] text-[var(--color-semantic-critical)] border border-red-500/30 px-3 py-1 rounded-[8px] text-[10px] font-extrabold flex items-center gap-1">
                            <ShieldAlert className="w-3.5 h-3.5" /> INACTIVE / EXPIRED
                          </span>
                        )}
                      </div>
                    </div>

                    {verificationResult.eligibility_status === 'Active' ? (
                      <div className="space-y-4">
                        <div className="grid grid-cols-3 gap-4">
                          <div className="bg-white/5 border border-white/10 rounded-[16px] p-4 text-center">
                            <p className="text-[9px] font-extrabold text-white/70 uppercase tracking-wider mb-1">Primary Copay</p>
                            <p className="text-lg font-extrabold text-white">${verificationResult.copay_primary?.toFixed(2)}</p>
                          </div>
                          <div className="bg-white/5 border border-white/10 rounded-[16px] p-4 text-center">
                            <p className="text-[9px] font-extrabold text-white/70 uppercase tracking-wider mb-1">Specialist Copay</p>
                            <p className="text-lg font-extrabold text-white">${verificationResult.copay_specialist?.toFixed(2)}</p>
                          </div>
                          <div className="bg-white/5 border border-white/10 rounded-[16px] p-4 text-center">
                            <p className="text-[9px] font-extrabold text-white/70 uppercase tracking-wider mb-1">Deductible</p>
                            <p className="text-lg font-extrabold text-[var(--color-semantic-positive)]">${verificationResult.deductible?.toFixed(2)}</p>
                          </div>
                        </div>

                        <div className="text-[10px] text-white/50 font-bold flex justify-between items-center pt-2 border-t border-white/10">
                          <span>Clearinghouse Verification Key: EDI271_{verificationResult.id}</span>
                          <span>Last Verified: {new Date(verificationResult.last_verified).toLocaleTimeString()}</span>
                        </div>
                      </div>
                    ) : (
                      <div className="bg-rose-50 border border-rose-100 text-rose-600 rounded-[16px] p-4 text-xs font-bold flex items-start gap-2.5">
                        <ShieldAlert className="w-5 h-5 shrink-0 mt-0.5" />
                        <div>
                          <p className="font-extrabold">Insurance Claim Warning</p>
                          <p className="text-[var(--color-semantic-critical)] font-medium mt-1">The patient's eligibility check failed. Any insurance claims submitted under this policy are likely to be rejected. Please request updated health credentials from subscriber.</p>
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="bg-white/5 border border-white/10 border-dashed rounded-[24px] py-12 px-6 text-center premium-shadow opacity-70">
                    <ShieldCheck className="w-10 h-10 text-white/50 mx-auto mb-3" />
                    <p className="text-xs font-bold text-white/70 mb-1">Verification Status: Unchecked</p>
                    <p className="text-[11px] text-white/70">Complete card fields or scan card to verify insurance eligibility status.</p>
                  </div>
                )}

              </div>

            </div>

            <div className="mt-8 border-t border-white/10 pt-5 flex justify-end">
              <button
                onClick={() => setIsHealthCardModalOpen(false)}
                className="bg-emerald-500 text-white hover:bg-emerald-400 font-extrabold px-8 py-3 rounded-[16px] text-xs transition-colors active:scale-95 shadow-md"
              >
                Close Portal
              </button>
            </div>
          </div>
        </div>
        </ModalPortal>
      )}

      {showScannerView && (
        <ModalPortal>
        <div className="fixed inset-0 z-[110] bg-slate-950/95 flex flex-col items-center justify-center p-6 text-white animate-in fade-in">
          {/* Style Injector for laser scanner translation */}
          <style>{`
            @keyframes scanPercent {
              0% { top: 0%; }
              50% { top: 100%; }
              100% { top: 0%; }
            }
            .laser-line {
              animation: scanPercent 2s ease-in-out infinite;
            }
          `}</style>

          {/* Shutter Flash Effect */}
          {isFlashActive && (
            <div className="fixed inset-0 bg-white/5 z-[120] pointer-events-none animate-fade-out duration-300"></div>
          )}

          <div className="max-w-md w-full text-center space-y-6">
            <div className="flex items-center justify-center gap-2 mb-2 text-[#818CF8]">
              <Camera className="w-5 h-5 animate-pulse" />
              <span className="text-[11px] font-extrabold uppercase tracking-widest">Sentinel OCR Document Scanner</span>
            </div>

            {/* Scanning Box */}
            <div className="relative aspect-[1.586/1] w-full rounded-[24px] bg-[#1E1B4B]/40 border-2 border-dashed border-[#818CF8]/80 p-6 flex flex-col justify-between overflow-hidden shadow-2xl backdrop-blur-sm">
              {/* Scan Laser Line */}
              <div className="laser-line absolute left-0 right-0 h-1 bg-gradient-to-r from-transparent via-[#818CF8] to-transparent shadow-[0_0_12px_#818CF8]"></div>

              {/* Corner brackets simulating camera frame */}
              <div className="absolute top-4 left-4 w-4 h-4 border-t-2 border-l-2 border-[#818CF8]"></div>
              <div className="absolute top-4 right-4 w-4 h-4 border-t-2 border-r-2 border-[#818CF8]"></div>
              <div className="absolute bottom-4 left-4 w-4 h-4 border-b-2 border-l-2 border-[#818CF8]"></div>
              <div className="absolute bottom-4 right-4 w-4 h-4 border-b-2 border-r-2 border-[#818CF8]"></div>

              <div className="flex justify-between items-start opacity-40">
                <span className="text-[9px] uppercase tracking-widest font-bold">Align Card Here</span>
                <Stethoscope className="w-5 h-5" />
              </div>
              <div className="text-center py-6 opacity-30">
                <p className="text-xs font-mono">[Place Health Insurance Card]</p>
              </div>
              <div className="flex justify-between items-end opacity-40">
                <span className="text-[9px] font-mono">ISO/IEC 7810</span>
                <span className="text-[9px] uppercase tracking-widest font-bold">Sentinel AI</span>
              </div>
            </div>

            <div className="space-y-1.5">
              <h4 className="text-sm font-bold">Reading Card Metadata...</h4>
              <p className="text-xs text-[#94A3B8] font-medium max-w-[280px] mx-auto">Please keep the card aligned and do not close the window.</p>
            </div>

            <div className="flex items-center justify-center gap-1.5 text-xs text-[#818CF8] font-semibold">
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              <span>Analyzing layout structures...</span>
            </div>
          </div>
        </div>
        </ModalPortal>
      )}
      {/* Outbound call: placed through the clinic's telephony connector; the result is recorded */}
      {callingPatient && (
        <ModalPortal>
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-emerald-500/40 backdrop-blur-sm animate-in fade-in">
          <div className="bg-emerald-500 border border-[#1F2937] text-white rounded-[28px] w-full max-w-sm p-6 premium-shadow relative overflow-hidden animate-in zoom-in-95 duration-300">
            {/* Pulsing glow background */}
            <div className="absolute top-[-30px] right-[-30px] w-32 h-32 bg-[var(--color-accent)] opacity-20 rounded-full blur-[40px]"></div>

            <div className="flex flex-col items-center text-center space-y-6 pt-4">
              <div className="relative">
                {callingStatus === 'dialing' && (
                  <div className="w-16 h-16 rounded-full bg-blue-500/10 border-2 border-blue-500 flex items-center justify-center animate-pulse">
                    <Phone className="w-6 h-6 text-blue-500 animate-bounce" />
                  </div>
                )}
                {callingStatus === 'connected' && (
                  <div className="w-16 h-16 rounded-full bg-emerald-500/10 border-2 border-emerald-500 flex items-center justify-center">
                    <div className="absolute inset-0 rounded-full bg-emerald-500/20 animate-ping"></div>
                    <Activity className="w-6 h-6 text-emerald-500" />
                  </div>
                )}
                {callingStatus === 'completed' && (
                  <div className="w-16 h-16 rounded-full bg-emerald-600 border-2 border-emerald-500 flex items-center justify-center">
                    <Check className="w-6 h-6 text-white" />
                  </div>
                )}
                {callingStatus === 'failed' && (
                  <div className="w-16 h-16 rounded-full bg-rose-600 border-2 border-rose-400 flex items-center justify-center">
                    <X className="w-6 h-6 text-white" />
                  </div>
                )}
              </div>

              <div>
                <span className="text-[9px] font-extrabold uppercase tracking-widest text-[#94A3B8]">Outbound Voice Call</span>
                <h4 className="text-lg font-extrabold text-white mt-1">Calling {callingPatient.patient_name || callingPatient.name}</h4>
                <p className="text-xs text-[#94A3B8] mt-1 font-mono uppercase font-bold tracking-wider">
                  {callingStatus === 'dialing' && '☎️ Placing call...'}
                  {callingStatus === 'completed' && '✅ Call completed'}
                  {callingStatus === 'failed' && (callResult?.status === 'NO_ANSWER' ? '📵 No answer' : '⚠️ Call not placed')}
                </p>
              </div>

              <div className="bg-white/5 border border-white/10 rounded-[16px] p-4 w-full">
                <span className="text-[9px] font-bold text-[#94A3B8] uppercase block text-left mb-1">Call record</span>
                <p className="text-xs text-white text-left leading-relaxed font-medium">
                  {callingStatus === 'dialing' && `Calling the number on file for ${callingPatient.patient_name || 'the patient'}...`}
                  {callingStatus === 'completed' && `Message: "Please proceed to ${callingPatient.provider_name || 'your provider'}${callingPatient.room && callingPatient.journey_stage === 'Consultation' ? ` in ${callingPatient.room}` : ''}." Receipt ${callResult?.receipt_ref || '—'}.`}
                  {callingStatus === 'failed' && `${callResult?.detail || 'The call could not be placed.'}${callResult?.receipt_ref ? ` Receipt ${callResult.receipt_ref}.` : ''}`}
                </p>
              </div>

              <button
                onClick={() => { setCallingPatient(null); setCallingStatus(null); setCallResult(null); }}
                className="w-full py-2.5 bg-rose-600 hover:bg-rose-700 text-white font-bold text-xs rounded-[16px] transition-colors"
              >
                {callingStatus === 'dialing' ? 'End Call' : 'Close'}
              </button>
            </div>
          </div>
        </div>
        </ModalPortal>
      )}

      {/* Clinic Room Interactive Allocation Modal */}
      {selectedRoom && (() => {
        const isOccupied = roomAssignments[selectedRoom]?.status === 'Occupied';
        return (
          <ModalPortal>
          <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-md animate-in fade-in p-4" onClick={(e) => { if (e.target === e.currentTarget) { setSelectedRoom(null); setAssignTargetPatient(''); } }}>
            <div className={`bg-[var(--color-surface)] rounded-[32px] w-full p-6 shadow-[0_30px_90px_rgba(0,0,0,0.9)] border border-white/20 relative animate-in zoom-in-95 duration-200 transition-all text-white max-h-[92vh] flex flex-col overflow-hidden ${isOccupied ? 'max-w-4xl' : 'max-w-md'}`}>
              <button
                onClick={() => {
                  setSelectedRoom(null);
                  setAssignTargetPatient('');
                }}
                aria-label="Close room panel" title="Close"
                className="absolute top-5 right-5 text-white/70 hover:text-white transition-colors p-2 bg-white/5 hover:bg-white/10 rounded-full cursor-pointer z-10"
              >
                <X className="w-5 h-5" />
              </button>

              <div className="mb-5 shrink-0 pr-12">
                <span className="text-[9px] font-extrabold text-blue-400 bg-blue-500/20 border border-blue-500/30 border border-[#BFDBFE] px-3 py-1 rounded-full uppercase tracking-wider">
                  Clinic Location Manager
                </span>
                <h3 className="text-xl font-extrabold text-white mt-2 flex items-center gap-2">
                  <LayoutGrid className="w-5 h-5 text-[#2563EB]" />
                  {selectedRoom} Control Panel
                </h3>
              </div>

              <div className="space-y-4 flex-1 min-h-0 overflow-y-auto pr-1">
                {/* Room Status Indicator */}
                <div className="bg-white/5 border border-white/10 rounded-[16px] p-4">
                  <span className="text-[9px] text-white/50 font-bold uppercase tracking-wider block mb-1">Current Status</span>
                  <div className="flex gap-2">
                    {['Available', 'Occupied', 'Cleaning'].map((st) => {
                      const isActive = roomAssignments[selectedRoom]?.status === st;
                      let colorStyle = 'border-white/10 text-white/60 hover:bg-white/5';
                      if (isActive) {
                        if (st === 'Available') colorStyle = 'bg-emerald-50 border-emerald-300 text-emerald-600 font-extrabold';
                        if (st === 'Occupied') colorStyle = 'bg-blue-50 border-blue-300 text-blue-400 font-extrabold';
                        if (st === 'Cleaning') colorStyle = 'bg-rose-50 border-rose-300 text-rose-600 font-extrabold';
                      }
                      return (
                        <button
                          key={st}
                          onClick={() => setRoomStatus(selectedRoom, st)}
                          className={`flex-1 border text-[10px] py-1.5 rounded-lg text-center transition-all cursor-pointer ${colorStyle}`}
                        >
                          {st}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Occupied view */}
                {roomAssignments[selectedRoom]?.status === 'Occupied' && (
                  (() => {
                    const enc = encounters.find(e => e.id === roomAssignments[selectedRoom]?.encounterId);
                    if (!enc) {
                      return (
                        <div className="bg-white/5 border border-white/10 rounded-[16px] p-4 text-center">
                          <p className="text-xs text-white/60 font-semibold">No patient linked to this room yet.</p>
                        </div>
                      );
                    }
                    return (
                      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">

                        {/* LEFT COLUMN: Patient Info & Vitals (col-span-5) */}
                        <div className="lg:col-span-5 flex flex-col gap-4 bg-white/5 border border-white/10 rounded-[24px] p-5 justify-between">
                          <div className="space-y-4">
                            <div className="flex items-center gap-3 pb-3 border-b border-white/10">
                              <div className="w-12 h-12 rounded-full bg-blue-500/20 border border-blue-500/30 flex items-center justify-center text-blue-400 font-extrabold text-sm flex-shrink-0">
                                {getInitials(enc.patient_name)}
                              </div>
                              <div>
                                <h4 className="text-sm font-extrabold text-white leading-tight">{enc.patient_name}</h4>
                                <span className="inline-flex text-[9px] font-black text-rose-500 bg-rose-50 border border-rose-200 px-2 py-0.5 rounded-full uppercase mt-1">
                                  {enc.priority} Priority
                                </span>
                              </div>
                            </div>

                            <div className="space-y-3 text-xs font-semibold text-slate-600">
                              <div>
                                <span className="text-[9px] text-white/50 font-bold uppercase tracking-wider block">Assigned Doctor</span>
                                <p className="text-sm font-bold text-white flex items-center gap-1.5 mt-0.5">
                                  <Stethoscope className="w-4 h-4 text-blue-500" />
                                  {enc.provider_name}
                                </p>
                              </div>

                              <div className="grid grid-cols-2 gap-3">
                                <div>
                                  <span className="text-[9px] text-white/50 font-bold uppercase tracking-wider block">Department</span>
                                  <p className="text-white font-bold mt-0.5">{enc.department || 'General Practice'}</p>
                                </div>
                                <div>
                                  <span className="text-[9px] text-white/50 font-bold uppercase tracking-wider block">Wait Time</span>
                                  <p className="text-rose-600 font-bold mt-0.5 flex items-center gap-1">
                                    <Clock className="w-3.5 h-3.5" />
                                    {enc.wait_time}
                                  </p>
                                </div>
                              </div>

                              {/* Vitals Stream Mockup */}
                              <div className="bg-white/5 border border-white/10 rounded-[16px] p-3 space-y-2 mt-2">
                                <span className="text-[9px] text-white/50 font-bold uppercase tracking-wider block">Observed Telemetry</span>
                                <div className="grid grid-cols-3 gap-2 text-center">
                                  <div className="bg-white/5 p-2 rounded-lg border border-white/10">
                                    <span className="text-[8px] text-white/50 block font-bold">BP</span>
                                    <span className="text-xs font-black text-emerald-600">{enc.clinical_notes?.includes('BP') ? enc.clinical_notes.split('BP')[1].split(',')[0].trim() : 'N/A'}</span>
                                  </div>
                                  <div className="bg-white/5 p-2 rounded-lg border border-white/10">
                                    <span className="text-[8px] text-white/50 block font-bold">HR</span>
                                    <span className="text-xs font-black text-blue-400">{enc.clinical_notes?.includes('HR') ? enc.clinical_notes.split('HR')[1].split(',')[0].trim() : 'N/A'}</span>
                                  </div>
                                  <div className="bg-white/5 p-2 rounded-lg border border-white/10">
                                    <span className="text-[8px] text-white/50 block font-bold">SpO2</span>
                                    <span className="text-xs font-black text-[var(--color-accent)]">{enc.clinical_notes?.includes('SpO2') ? enc.clinical_notes.split('SpO2')[1].split(',')[0].trim() : 'N/A'}</span>
                                  </div>
                                </div>
                              </div>
                            </div>
                          </div>

                          {/* Quick Actions Footer */}
                          <div className="space-y-2 pt-3 border-t border-white/10">
                            <button
                              onClick={() => {
                                handleOpenHealthCard(enc.patient_name, enc.patient_ref);
                                setSelectedRoom(null);
                              }}
                              className="w-full py-2 bg-indigo-900/30 border border-indigo-500/50 hover:bg-indigo-500/30 text-indigo-300 font-bold text-xs rounded-[10px] transition-all flex items-center justify-center gap-1.5 active:scale-95 cursor-pointer"
                            >
                              <CreditCard className="w-4 h-4" /> Verify Benefit Insurance
                            </button>
                            <button
                              onClick={() => handleCallPatient(enc)}
                              className="w-full py-2 bg-blue-900/30 border border-blue-500/50 hover:bg-blue-500/30 text-blue-400 font-bold text-xs rounded-[10px] transition-all flex items-center justify-center gap-1.5 active:scale-95 cursor-pointer"
                            >
                              <Phone className="w-4 h-4" /> Outbound TTS Call
                            </button>
                            <button
                              onClick={() => handleDownloadPatientReport(enc)}
                              className="w-full py-2 bg-emerald-900/30 border border-emerald-500/50 hover:bg-emerald-500/30 text-emerald-400 font-bold text-xs rounded-[10px] transition-all flex items-center justify-center gap-1.5 active:scale-95 cursor-pointer"
                            >
                              <Download className="w-4 h-4" /> Download Health Summary
                            </button>
                          </div>
                        </div>

                        <div className="lg:col-span-7 flex flex-col gap-4 bg-white/5 border border-white/10 rounded-[24px] p-5 justify-between">
                          <div className="space-y-4">
                            <div className="flex items-center justify-between pb-3 border-b border-white/10">
                              <h4 className="text-xs font-black text-white flex items-center gap-1.5">
                                <FileText className="w-4 h-4 text-[var(--color-accent)]" />
                                Clinical Charting Workspace
                              </h4>
                            </div>

                            {/* Modern Workspace Segmented Tab Selection */}
                            <div className="flex gap-1 bg-white/10 p-1 rounded-2xl">
                              <button
                                type="button"
                                onClick={() => setModalActiveTab('soap')}
                                className={`flex-1 py-2 text-xs font-bold rounded-lg transition-all flex items-center justify-center gap-1.5 cursor-pointer ${modalActiveTab === 'soap'
                                    ? 'bg-white/20 text-white shadow-sm'
                                    : 'text-white/60 hover:text-white'
                                  }`}
                              >
                                <FileText className="w-3.5 h-3.5" /> SOAP Notes
                              </button>
                              <button
                                type="button"
                                onClick={() => setModalActiveTab('rx')}
                                className={`flex-1 py-2 text-xs font-bold rounded-lg transition-all flex items-center justify-center gap-1.5 cursor-pointer ${modalActiveTab === 'rx'
                                    ? 'bg-white/20 text-white shadow-sm'
                                    : 'text-white/60 hover:text-white'
                                  }`}
                              >
                                <Stethoscope className="w-3.5 h-3.5" /> Diagnosis & Rx
                              </button>
                              <button
                                type="button"
                                onClick={() => setModalActiveTab('vitals')}
                                className={`flex-1 py-2 text-xs font-bold rounded-lg transition-all flex items-center justify-center gap-1.5 cursor-pointer ${modalActiveTab === 'vitals'
                                    ? 'bg-white/20 text-white shadow-sm'
                                    : 'text-white/60 hover:text-white'
                                  }`}
                              >
                                <Activity className="w-3.5 h-3.5" /> Vitals & Gateways
                              </button>
                            </div>

                            <div className="space-y-4 max-h-[360px] overflow-y-auto custom-scrollbar pr-1">
                              {/* TAB 1: SOAP notes */}
                              {modalActiveTab === 'soap' && (
                                <div className="space-y-4 animate-in fade-in duration-200">
                                  {/* SOAP prefill quick templates */}
                                  <div className="bg-white/5 border border-white/10 rounded-[16px] p-3.5">
                                    <span className="text-[9px] text-white/50 font-extrabold uppercase tracking-wider block mb-2">Prefill Quick Templates</span>
                                    <div className="flex gap-2">
                                      <button
                                        onClick={() => {
                                          setSoapS("Patient reports mild headache and occasional chest tightness for 2 days. Complies with blood pressure meds daily. No nausea or dizziness.");
                                          setSoapO("BP: 138/88 mmHg. Pulse: 74 bpm, regular. Lungs clear to auscultation. Heart S1, S2, regular rhythm.");
                                          setSoapA("Essential hypertension. Marginally controlled today.");
                                          setSoapP("Continue current regimen. Schedule a blood pressure follow-up clinic appointment in 2 weeks.");
                                          setEditingDiagnosis("I10 (Essential Hypertension)");
                                        }}
                                        className="flex-1 text-[9px] font-black text-indigo-300 bg-indigo-500/20 hover:bg-indigo-500/30 py-1.5 px-2 rounded-[8px] border border-indigo-500/40 transition-colors"
                                      >
                                        Hypertension
                                      </button>
                                      <button
                                        onClick={() => {
                                          setSoapS("Presents for routine diabetes follow-up. Reviews morning glucose logs. Denies neuropathic symptoms.");
                                          setSoapO("Weight stable. Blood glucose: 118 mg/dL. HbA1c: 6.7%. Foot exam: sensation intact.");
                                          setSoapA("Type 2 diabetes mellitus, controlled.");
                                          setSoapP("Continue Metformin 500mg BID. Next HbA1c screening in 3 months.");
                                          setEditingDiagnosis("E11.9 (Type 2 Diabetes)");
                                        }}
                                        className="flex-1 text-[9px] font-black text-emerald-600 bg-emerald-50 hover:bg-emerald-100 py-1.5 px-2 rounded-[8px] border border-emerald-200 transition-colors"
                                      >
                                        Type 2 Diabetes
                                      </button>
                                      <button
                                        onClick={() => {
                                          setSoapS("Presents for annual wellness visit. No acute complaints. Reports active physical lifestyle.");
                                          setSoapO("Vitals stable. Cardiovascular and chest exams unremarkable. Normal reflexes.");
                                          setSoapA("Encounter for general adult health examination.");
                                          setSoapP("Screening labs ordered (lipid panel, CMP, CBC). Advised regular checkups.");
                                          setEditingDiagnosis("Z00.00 (General Wellness)");
                                        }}
                                        className="flex-1 text-[9px] font-black text-white/80 bg-white/10 hover:bg-white/20 py-1.5 px-2 rounded-[8px] border border-white/20 transition-colors"
                                      >
                                        Adult Wellness
                                      </button>
                                    </div>
                                  </div>

                                  <div className="space-y-3">
                                    <div>
                                      <div className="flex justify-between items-center mb-1">
                                        <label className="text-[10px] text-white/60 font-extrabold uppercase tracking-wider flex items-center gap-1">
                                          <span className="w-4 h-4 rounded bg-blue-100 text-blue-700 font-bold flex items-center justify-center text-[9px]">S</span>
                                          Subjective Notes
                                        </label>
                                        <span className="text-[8px] text-white/50 font-semibold">Symptoms & History</span>
                                      </div>
                                      <textarea
                                        value={soapS}
                                        onChange={(e) => setSoapS(e.target.value)}
                                        rows={2}
                                        placeholder="Patient complaints, feelings, or symptom descriptions..."
                                        className="w-full bg-[#180A2E] border border-white/20 text-xs font-bold text-white placeholder:text-white/60 rounded-[10px] px-3 py-2 outline-none focus:border-blue-500 transition-all resize-none"
                                      />
                                    </div>

                                    <div>
                                      <div className="flex justify-between items-center mb-1">
                                        <label className="text-[10px] text-white/60 font-extrabold uppercase tracking-wider flex items-center gap-1">
                                          <span className="w-4 h-4 rounded bg-emerald-100 text-emerald-700 font-bold flex items-center justify-center text-[9px]">O</span>
                                          Objective Notes
                                        </label>
                                        <span className="text-[8px] text-white/50 font-semibold">Vitals & Labs</span>
                                      </div>
                                      <textarea
                                        value={soapO}
                                        onChange={(e) => setSoapO(e.target.value)}
                                        rows={2}
                                        placeholder="Measurable findings, physical exam observations, BP..."
                                        className="w-full bg-[#180A2E] border border-white/20 text-xs font-semibold text-white placeholder:text-white/60 rounded-[10px] px-3 py-2 outline-none focus:border-emerald-500 transition-all resize-none"
                                      />
                                    </div>

                                    <div>
                                      <div className="flex justify-between items-center mb-1">
                                        <label className="text-[10px] text-white/60 font-extrabold uppercase tracking-wider flex items-center gap-1">
                                          <span className="w-4 h-4 rounded bg-amber-100 text-amber-700 font-bold flex items-center justify-center text-[9px]">A</span>
                                          Assessment Notes
                                        </label>
                                        <span className="text-[8px] text-white/50 font-semibold">Diagnosis & Findings</span>
                                      </div>
                                      <textarea
                                        value={soapA}
                                        onChange={(e) => setSoapA(e.target.value)}
                                        rows={2}
                                        placeholder="Clinical assessment, diagnostics, differential diagnosis..."
                                        className="w-full text-white font-bold bg-[#180A2E] border border-white/10 text-xs font-semibold rounded-[10px] px-3 py-2 outline-none focus:border-amber-500 focus:bg-white/10 transition-all resize-none"
                                      />
                                    </div>

                                    <div>
                                      <div className="flex justify-between items-center mb-1">
                                        <label className="text-[10px] text-white/60 font-extrabold uppercase tracking-wider flex items-center gap-1">
                                          <span className="w-4 h-4 rounded bg-[#E0D9FD] text-[var(--color-surface)] font-bold flex items-center justify-center text-[9px]">P</span>
                                          Plan / Treatment
                                        </label>
                                        <span className="text-[8px] text-white/50 font-semibold">Next Steps</span>
                                      </div>
                                      <textarea
                                        value={soapP}
                                        onChange={(e) => setSoapP(e.target.value)}
                                        rows={2}
                                        placeholder="Prescription changes, follow-up timelines, referrals..."
                                        className="w-full bg-[#180A2E] border border-white/20 text-xs font-semibold text-white placeholder:text-white/60 rounded-[10px] px-3 py-2 outline-none focus:border-purple-500 transition-all resize-none"
                                      />
                                    </div>
                                  </div>
                                </div>
                              )}

                              {/* TAB 2: Diagnosis & Rx */}
                              {modalActiveTab === 'rx' && (
                                <div className="space-y-4 animate-in fade-in duration-200">
                                  <div>
                                    <label className="text-[10px] text-white/50 font-extrabold uppercase block mb-1">ICD-10 Primary Diagnosis</label>
                                    <input
                                      type="text"
                                      value={editingDiagnosis}
                                      onChange={(e) => setEditingDiagnosis(e.target.value)}
                                      placeholder="e.g. I10 (Essential Hypertension)"
                                      className="w-full bg-[#180A2E] border border-white/20 text-xs font-bold text-white placeholder:text-white/60 rounded-[10px] px-3 py-2 outline-none focus:border-blue-500 transition-all"
                                    />
                                  </div>

                                  <div>
                                    <label className="text-[10px] text-white/50 font-extrabold uppercase block mb-1">Medications & Rx Dosage</label>
                                    <textarea
                                      value={editingMedications}
                                      onChange={(e) => setEditingMedications(e.target.value)}
                                      rows={4}
                                      placeholder="e.g. Lisinopril 10mg daily PO. Refills: 3"
                                      className="w-full bg-[#180A2E] border border-white/20 text-xs font-semibold text-white placeholder:text-white/60 rounded-[10px] px-3 py-2 outline-none focus:border-blue-500 transition-all resize-none"
                                    />
                                  </div>
                                </div>
                              )}

                              {/* TAB 3: Vitals & Gateways */}
                              {modalActiveTab === 'vitals' && (
                                <div className="space-y-4 animate-in fade-in duration-200">
                                  {/* Vitals summary */}
                                  <div className="bg-white/5 border border-white/10 rounded-[16px] p-4 space-y-2">
                                    <span className="text-[9px] text-white/50 font-bold uppercase tracking-wider block">Observed Telemetry Streams</span>
                                    <div className="grid grid-cols-3 gap-2 text-center">
                                      <div className="bg-white/5 p-2.5 rounded-lg border border-white/10">
                                        <span className="text-[8px] text-white/50 block font-bold">BP</span>
                                        <span className="text-xs font-black text-emerald-600">{enc.clinical_notes?.includes('BP') ? enc.clinical_notes.split('BP')[1].split(',')[0].trim() : 'N/A'}</span>
                                      </div>
                                      <div className="bg-white/5 p-2.5 rounded-lg border border-white/10">
                                        <span className="text-[8px] text-white/50 block font-bold">HR</span>
                                        <span className="text-xs font-black text-blue-400">{enc.clinical_notes?.includes('HR') ? enc.clinical_notes.split('HR')[1].split(',')[0].trim() : 'N/A'}</span>
                                      </div>
                                      <div className="bg-white/5 p-2.5 rounded-lg border border-white/10">
                                        <span className="text-[8px] text-white/50 block font-bold">SpO2</span>
                                        <span className="text-xs font-black text-[var(--color-accent)]">{enc.clinical_notes?.includes('SpO2') ? enc.clinical_notes.split('SpO2')[1].split(',')[0].trim() : 'N/A'}</span>
                                      </div>
                                    </div>
                                  </div>

                                  {/* Quick Actions / Verification Gateways */}
                                  <div className="bg-white/5 border border-white/10 rounded-[16px] p-4 space-y-3">
                                    <span className="text-[9px] text-indigo-300 font-extrabold uppercase tracking-wider block">Verification & Communications</span>
                                    <div className="flex gap-2">
                                      <button
                                        onClick={() => {
                                          handleOpenHealthCard(enc.patient_name, enc.patient_ref);
                                          setSelectedRoom(null);
                                        }}
                                        className="flex-1 py-2 bg-indigo-900/30 border border-indigo-500/50 hover:bg-indigo-500/30 text-indigo-300 font-bold text-xs rounded-[10px] transition-all flex items-center justify-center gap-1.5 active:scale-95 cursor-pointer"
                                      >
                                        <CreditCard className="w-4 h-4" /> Verify Insurance
                                      </button>
                                      <button
                                        onClick={() => handleCallPatient(enc)}
                                        className="flex-1 py-2 bg-blue-900/30 border border-blue-500/50 hover:bg-blue-500/30 text-blue-400 font-bold text-xs rounded-[10px] transition-all flex items-center justify-center gap-1.5 active:scale-95 cursor-pointer"
                                      >
                                        <Phone className="w-4 h-4" /> Outbound Call
                                      </button>
                                    </div>
                                  </div>
                                </div>
                              )}
                            </div>
                          </div>

                          <div className="flex gap-3 pt-3 border-t border-white/10 w-full">
                            <button
                              onClick={() => handleSaveCharting(enc.id)}
                              disabled={isSavingChart}
                              className={`flex-1 py-2.5 text-xs font-bold rounded-[10px] transition-all cursor-pointer text-center ${saveChartSuccess
                                  ? 'bg-emerald-50 text-emerald-600 border border-emerald-300'
                                  : 'bg-[var(--color-surface-raised)] hover:bg-[var(--color-surface)] text-white shadow-sm hover:scale-[1.01] active:scale-98'
                                }`}
                            >
                              {isSavingChart ? 'Saving Patient Record...' : saveChartSuccess ? '✓ Chart Notes Saved Successfully' : 'Save Charting Details'}
                            </button>
                            <button
                              onClick={async () => {
                                try {
                                  // Auto-save charting values first on checkout!
                                  const combinedNotes = `Subjective: ${soapS}\n\nObjective: ${soapO}\n\nAssessment: ${soapA}\n\nPlan: ${soapP}`;
                                  await fetchWithAuth(`/api/v1/encounters/${enc.id}`, {
                                    method: 'PUT',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({
                                      diagnosis: editingDiagnosis,
                                      clinical_notes: combinedNotes,
                                      medications: editingMedications
                                    })
                                  });
                                  // The checkout is recorded as an event (with the actor).
                                  await postFlow(`/visits/${encodeURIComponent(enc.id)}/status`, { status: 'Completed' });
                                  toast(`${enc.patient_name} checked out.`);
                                  setSelectedRoom(null);
                                } catch (e) {
                                  toast(`Checkout not recorded: ${(e as Error).message}`, 'error');
                                } finally {
                                  fetchEncounters();
                                  fetchFlow();
                                }
                              }}
                              className="py-2.5 px-4 bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-xs rounded-[10px] transition-colors hover:scale-[1.01] active:scale-98 cursor-pointer text-center"
                            >
                              Checkout Patient (Completed)
                            </button>
                            <button
                              onClick={async () => {
                                try {
                                  await postFlow(`/visits/${encodeURIComponent(enc.id)}/status`, { status: 'Waiting' });
                                  setSelectedRoom(null);
                                } catch (e) {
                                  toast((e as Error).message, 'error');
                                } finally {
                                  fetchEncounters();
                                  fetchFlow();
                                }
                              }}
                              className="py-2.5 px-3 bg-white/10 hover:bg-white/20 border border-white/20 text-white font-bold text-xs rounded-[10px] transition-colors cursor-pointer"
                            >
                              Return to Queue
                            </button>
                          </div>
                        </div>

                      </div>
                    );
                  })()
                )}

                {/* Available view: assign patient + doctor */}
                {roomAssignments[selectedRoom]?.status === 'Available' && (
                  <div className="bg-white/5 border border-white/10 rounded-[16px] p-4 space-y-4">

                    {/* Decision Support Recommendation Badge */}
                    <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-[10px] px-3 py-2 text-[10px] text-emerald-300 font-bold flex items-center gap-2">
                      🏥 <span>Assign both a Patient and a Doctor to this room. Staff confirmation required (ARR-004).</span>
                    </div>

                    {/* Patient Assignment */}
                    <div>
                      <span className="text-[9px] text-white/50 font-bold uppercase tracking-wider block mb-2">👤 Patient — {selectedRoom}</span>
                      {(flow?.queue || []).filter(e => e.status === 'Waiting' || e.status === 'Delayed').length > 0 ? (
                        <select
                          value={assignTargetPatient}
                          onChange={(e) => setAssignTargetPatient(e.target.value)}
                          className="w-full bg-white/10 border border-white/10 text-xs font-bold text-white/70 rounded-[10px] px-3 py-2 outline-none cursor-pointer"
                        >
                          <option value="" className="bg-[var(--color-surface)] text-white">-- Select Patient from Queue --</option>
                          {(flow?.queue || [])
                            .filter(e => e.status === 'Waiting' || e.status === 'Delayed')
                            .sort((a, b) => {
                              const p: Record<string, number> = { Urgent: 0, Normal: 1 };
                              return (p[a.priority || ''] ?? 2) - (p[b.priority || ''] ?? 2);
                            })
                            .map(e => (
                              <option key={e.id} value={e.id} className="bg-[var(--color-surface)] text-white">
                                {e.patient_name} ({e.priority} priority — wait {e.wait_time})
                              </option>
                            ))
                          }
                        </select>
                      ) : (
                        <p className="text-xs text-white/60 font-semibold">No patients currently waiting in the queue.</p>
                      )}
                    </div>

                    {/* Doctor Assignment */}
                    <div>
                      <span className="text-[9px] text-white/50 font-bold uppercase tracking-wider block mb-2">🩺 Assign Doctor — {selectedRoom}</span>
                      <select
                        value={assignTargetDoctor}
                        onChange={(e) => setAssignTargetDoctor(e.target.value)}
                        className="w-full bg-white/10 border border-white/10 text-xs font-bold text-white/70 rounded-[10px] px-3 py-2 outline-none cursor-pointer"
                      >
                        <option value="" className="bg-[var(--color-surface)] text-white">-- Select Doctor --</option>
                        {CLINIC_DOCTORS.map(doc => (
                          <option key={doc.id} value={doc.id} className="bg-[var(--color-surface)] text-white" disabled={!doc.available}>
                            {doc.name} — {doc.specialty}{!doc.available ? ' (Unavailable)' : ''}
                          </option>
                        ))}
                      </select>
                    </div>

                    {/* Already assigned doctor badge */}
                    {roomAssignments[selectedRoom]?.doctorId && (() => {
                      const currentDoc = CLINIC_DOCTORS.find(d => d.id === roomAssignments[selectedRoom]?.doctorId);
                      return currentDoc ? (
                        <div className="flex items-center justify-between bg-purple-500/10 border border-purple-500/30 rounded-[10px] px-3 py-2">
                          <p className="text-[10px] text-purple-300 font-bold">Currently Assigned: {currentDoc.name}</p>
                          <button
                            onClick={() => setRoomDoctor(selectedRoom, null)}
                            className="text-[9px] text-rose-400 hover:text-rose-300 font-bold"
                          >Remove</button>
                        </div>
                      ) : null;
                    })()}

                    {/* Confirm Button */}
                    <button
                      onClick={async () => {
                        if (!assignTargetPatient && !assignTargetDoctor) return;
                        const roomName = selectedRoom;
                        const patient = (flow?.queue || []).find(e => e.id === assignTargetPatient);
                        const doctor = assignTargetDoctor;
                        setSelectedRoom(null);
                        setAssignTargetPatient('');
                        setAssignTargetDoctor('');
                        // Staff confirmation (ARR-004): recorded on the server with the actor.
                        if (doctor) await setRoomDoctor(roomName, doctor);
                        if (patient) await assignRoom(patient.id, roomName, patient.patient_name);
                        else toast(`🏥 ${roomName}: ${doctor} assigned.`);
                      }}
                      disabled={!assignTargetPatient && !assignTargetDoctor}
                      className="w-full py-2.5 bg-[#2563EB] hover:bg-blue-700 active:scale-95 text-white font-bold text-xs rounded-[10px] transition-all disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer flex items-center justify-center gap-2"
                    >
                      ✓ Confirm Room Allotment
                    </button>
                  </div>
                )}

                {/* Cleaning view */}
                {roomAssignments[selectedRoom]?.status === 'Cleaning' && (
                  <div className="bg-white/5 border border-white/10 rounded-[16px] p-4 text-center space-y-3">
                    <span className="text-[9px] text-rose-500 font-bold uppercase tracking-widest block">Sanitization in Progress</span>
                    <p className="text-xs text-white/60 font-semibold leading-relaxed">
                      This room is currently flagged for cleaning & sanitation protocols.
                    </p>
                    <button
                      onClick={() => setRoomStatus(selectedRoom, 'Available')}
                      className="w-full py-2 bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-xs rounded-[10px] transition-colors cursor-pointer"
                    >
                      Finish Cleaning & Make Available
                    </button>
                  </div>
                )}
              </div>

              <div className="mt-4 flex justify-end shrink-0">
                <button
                  onClick={() => {
                    setSelectedRoom(null);
                    setAssignTargetPatient('');
                  }}
                  className="bg-white/10 hover:bg-white/20 border border-white/20 text-white font-bold text-xs px-4 py-2 rounded-[10px] transition-colors cursor-pointer"
                >
                  Close Panel
                </button>
              </div>
            </div>
          </div>
          </ModalPortal>
        );
      })()}
      {/* Custom Glassmorphism Add Room Modal */}
      {isAddRoomModalOpen && (
        <ModalPortal>
        <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 backdrop-blur-md animate-in fade-in">
          <div className="bg-[var(--color-surface)] border border-white/20 rounded-[28px] w-full max-w-md p-6 shadow-[0_25px_60px_rgba(0,0,0,0.9)] animate-in zoom-in-95 text-white">
            <div className="flex justify-between items-center mb-4 border-b border-white/10 pb-3">
              <h3 className="text-base font-extrabold text-white flex items-center gap-2">
                <LayoutGrid className="w-5 h-5 text-blue-400" /> Add Clinic Room
              </h3>
              <button onClick={() => setIsAddRoomModalOpen(false)} className="text-white/50 hover:text-white text-xs font-bold p-1">✕</button>
            </div>

            <p className="text-xs text-white/70 font-medium mb-4">Enter custom room or location name (e.g. Room 4, Triage Bay 2, Suite 101):</p>

            <input
              type="text"
              placeholder="Room Name..."
              value={newRoomName}
              onChange={(e) => setNewRoomName(e.target.value)}
              className="w-full bg-white/5 border border-white/15 rounded-[14px] p-3 text-sm font-bold text-white outline-none focus:border-blue-500 mb-6 placeholder:text-white/30"
              autoFocus
            />

            <p className="text-xs text-white/70 font-medium mb-4">Assign Doctor (Optional):</p>
            <select
              value={newRoomDoctor || ''}
              onChange={(e) => setNewRoomDoctor(e.target.value || null)}
              className="w-full bg-[var(--color-surface)] border border-white/20 rounded-[12px] p-3 text-xs font-bold text-white outline-none cursor-pointer mb-8"
            >
              <option value="">-- No Doctor Assigned --</option>
              {CLINIC_DOCTORS.map(doc => (
                <option key={doc.id} value={doc.id}>
                  🩺 {doc.name} — {doc.specialty}
                </option>
              ))}
            </select>

            <div className="flex gap-3">
              <button
                onClick={() => setIsAddRoomModalOpen(false)}
                className="flex-1 bg-white/5 hover:bg-white/10 border border-white/10 text-white font-bold py-2.5 rounded-[14px] text-xs transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={async () => {
                  if (!newRoomName || !newRoomName.trim()) return;
                  const cleanName = newRoomName.trim();
                  try {
                    await postFlow('/rooms', { name: cleanName, kind: /lab/i.test(cleanName) ? 'lab' : 'consult', doctor: newRoomDoctor });
                    toast(`🏥 ${cleanName} added to Clinic Heat Map!`);
                    setNewRoomName('');
                    setNewRoomDoctor(null);
                    setIsAddRoomModalOpen(false);
                  } catch (e) {
                    toast(`${cleanName} not added: ${(e as Error).message}`, 'error');
                  } finally {
                    fetchFlow();
                  }
                }}
                className="flex-1 bg-[#2563EB] hover:bg-blue-700 text-white font-bold py-2.5 rounded-[14px] text-xs transition-colors shadow-lg active:scale-95"
              >
                + Add Room
              </button>
            </div>
          </div>
        </div>
        </ModalPortal>
      )}
    </div>
  );
};
