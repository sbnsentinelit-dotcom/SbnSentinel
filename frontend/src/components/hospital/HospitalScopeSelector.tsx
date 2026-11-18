'use client';
export interface HospitalScope {
  org_id: string; clinic_id: string; allowed_department_ids: string[];
  all_departments: boolean; capabilities: string[]; grant_revision: number;
}
export interface HospitalFacility {
  clinic_id: string; name: string; scope: HospitalScope;
  last_import_at?: string | null;
  departments: { id: string; name: string }[];
}
export interface HospitalSelection { clinicId: string; departmentId: string; revision: number }
export function HospitalScopeSelector({ facilities, selection, onChange }: {
  facilities: HospitalFacility[]; selection: HospitalSelection | null;
  onChange: (value: HospitalSelection) => void;
}) {
  const facility = facilities.find(item => item.clinic_id === selection?.clinicId);
  return <div className="flex flex-wrap gap-4">
    <label>Facility <select aria-label="Hospital facility" value={selection?.clinicId ?? ''}
      onChange={event => {
        const next = facilities.find(item => item.clinic_id === event.target.value);
        if (next) onChange({ clinicId: next.clinic_id, departmentId: '', revision: next.scope.grant_revision });
      }}>
      <option value="" disabled>Select facility</option>
      {facilities.map(item => <option key={item.clinic_id} value={item.clinic_id}>{item.name}</option>)}
    </select></label>
    <label>Department <select aria-label="Hospital department" disabled={!facility}
      value={selection?.departmentId ?? ''} onChange={event => {
        if (selection) onChange({ ...selection, departmentId: event.target.value });
      }}>
      <option value="">All permitted departments</option>
      {facility?.departments.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label>
  </div>;
}
