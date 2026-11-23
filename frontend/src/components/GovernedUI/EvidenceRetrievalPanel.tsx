import React, { useEffect, useState } from 'react';
import { CheckCircle2, FileSearch, XCircle } from 'lucide-react';
import { EvidenceRetrieval, retrieveClaimEvidence } from '@/utils/autopilot';

/**
 * Human Decision page, denied claims only: Sentinel collects the evidence the payer needs from the
 * connected systems and lists what it found (with source and reference) and what it could not find,
 * so the reviewer can check it before approving the resubmission.
 */
export const EvidenceRetrievalPanel: React.FC<{ signalId: string }> = ({ signalId }) => {
  const [retrieval, setRetrieval] = useState<EvidenceRetrieval | null>(null);
  const [state, setState] = useState<'loading' | 'done' | 'failed'>('loading');

  useEffect(() => {
    let cancelled = false;
    setState('loading');
    retrieveClaimEvidence(signalId).then((r) => {
      if (cancelled) return;
      setRetrieval(r);
      setState(r ? 'done' : 'failed');
    });
    return () => { cancelled = true; };
  }, [signalId]);

  return (
    <div className="mt-4 bg-white/5 border border-white/10 rounded-[14px] p-4" aria-label="Claim evidence">
      <h6 className="text-[11px] font-extrabold text-white uppercase tracking-wider mb-2 flex items-center gap-2">
        <FileSearch className="w-4 h-4 text-indigo-300" /> Evidence collected for resubmission
      </h6>
      {state === 'loading' && <p className="text-xs text-white/60">Searching the connected systems…</p>}
      {state === 'failed' && <p className="text-xs text-red-300">Evidence could not be collected right now.</p>}
      {state === 'done' && retrieval && retrieval.documents.length === 0 && (
        <p className="text-xs text-white/60">No claim evidence applies to this signal.</p>
      )}
      {state === 'done' && retrieval && retrieval.documents.length > 0 && (
        <>
          <ul className="space-y-1.5">
            {retrieval.documents.map((d) => (
              <li key={d.name} className="flex items-start gap-2 text-xs">
                {d.status === 'FOUND'
                  ? <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0 mt-0.5" />
                  : <XCircle className="w-4 h-4 text-red-400 flex-shrink-0 mt-0.5" />}
                <span className="text-white/90">
                  <span className="font-bold">{d.name}</span>
                  {d.status === 'FOUND'
                    ? <span className="text-white/60"> — found in {d.source} ({d.document_ref})</span>
                    : <span className="text-red-300"> — not found in any connected system</span>}
                </span>
              </li>
            ))}
          </ul>
          <p className={`text-[11px] font-semibold mt-3 ${retrieval.complete ? 'text-emerald-300' : 'text-amber-300'}`}>
            {retrieval.complete
              ? 'All required evidence is available. Check it, then approve and resubmit the claim.'
              : `Missing: ${retrieval.not_found.join(', ')}. A notification was sent; the claim cannot be resubmitted until it is obtained.`}
          </p>
        </>
      )}
    </div>
  );
};
