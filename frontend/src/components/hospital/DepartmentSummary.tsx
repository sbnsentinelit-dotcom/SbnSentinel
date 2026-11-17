'use client';
import type { HospitalScope } from './HospitalScopeSelector';
export interface HospitalSnapshot {
  snapshot_id: string; scope: HospitalScope; currency: string; knowledge_cutoff: string;
  restricted: boolean; completeness_status: string; coverage_exceptions: string[];
  has_more?: boolean;
  metrics: { line_count: number; distinct_claim_count: number; known_billed_amount: string | null;
    known_paid_amount: string | null; unknown_payment_lines: number };
  rows: { line_id: string; department_id: string | null; billed_amount: string | null;
    paid_amount: string | null; status: string }[];
}
export function DepartmentSummary({ snapshot, departments = [] }: {
  snapshot: HospitalSnapshot; departments?: { id: string; name: string }[];
}) {
  // Work on decimal text: Number/Intl conversion can lose source precision.
  const money = (value: string | null) => {
    if (value === null) return 'Unavailable';
    const [whole, fraction] = value.split('.');
    const exact = fraction === undefined ? value : `${whole}.${fraction.replace(/0+$/, '').padEnd(2, '0')}`;
    return `${snapshot.currency} ${exact}`;
  };
  return <section aria-label="Department financial summary" className="space-y-3">
    <h2 className="text-lg font-semibold">Department summary</h2>
    <p>{snapshot.restricted ? 'Restricted to permitted lines' : 'Permitted hospital view'} · {snapshot.completeness_status}</p>
    <dl className="grid grid-cols-2 gap-3">
      <div><dt>Lines</dt><dd>{snapshot.metrics.line_count}</dd></div>
      <div><dt>Distinct claims in these lines</dt><dd>{snapshot.metrics.distinct_claim_count}</dd></div>
      <div><dt>Known billed amount</dt><dd>{money(snapshot.metrics.known_billed_amount)}</dd></div>
      <div><dt>Known adjudicated payment</dt><dd>{money(snapshot.metrics.known_paid_amount)}</dd></div>
      <div><dt>Lines with unknown payment</dt><dd>{snapshot.metrics.unknown_payment_lines}</dd></div>
    </dl>
    <p>Cash and whole-cohort receivable are unavailable without sufficient source evidence.</p>
    {snapshot.coverage_exceptions.map(item => <p key={item}>{item}</p>)}
    <p>Snapshot recorded: {snapshot.knowledge_cutoff}</p>
    {snapshot.has_more && <p>Showing the first 100 lines. The snapshot export includes all permitted lines.</p>}
    <div className="overflow-auto"><table className="w-full text-left">
      <thead><tr><th>Line</th><th>Department</th><th>Billed</th><th>Payment</th><th>Status</th></tr></thead>
      <tbody>{snapshot.rows.map(row => <tr key={row.line_id}>
        <td>{row.line_id}</td><td>{row.department_id === null ? 'Unattributed'
          : departments.find(item => item.id === row.department_id)?.name ?? 'Department name unavailable'}</td>
        <td>{money(row.billed_amount)}</td><td>{money(row.paid_amount)}</td><td>{row.status}</td>
      </tr>)}</tbody>
    </table></div>
  </section>;
}
