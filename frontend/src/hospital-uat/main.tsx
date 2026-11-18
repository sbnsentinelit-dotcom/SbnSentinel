import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { HospitalWorkspace } from '../components/hospital/HospitalWorkspace';
import './style.css';

function Sandbox() {
  const [actor, setActor] = useState('1');
  const [ready, setReady] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    setReady(''); setError('');
    void fetch(`/uat/session/${actor}`).then(async response => {
      if (!response.ok) throw new Error('Synthetic API unavailable');
      const data = await response.json();
      if (alive) { localStorage.setItem('token', data.token); setReady(actor); }
    }).catch(() => { if (alive) setError('Start the dedicated synthetic API on port 18001.'); });
    return () => { alive = false; };
  }, [actor]);
  return <main><h1>HSP01 synthetic UI test</h1>
    <p>Disposable in-memory data. Real hospital UI and scoped APIs. Restart resets all changes.</p>
    <p>Source import, approved billing decisions and full FR01–FR08 automation are not represented by these fixtures.</p>
    <label>Test account <select aria-label="Synthetic test account" value={actor} onChange={event => setActor(event.target.value)}>
      <option value="1">Hospital reviewer</option><option value="2">Cardiology only</option>
      <option value="3">Radiology only</option><option value="4">No hospital access</option>
    </select></label>
    {error && <p role="alert">{error}</p>}
    {ready === actor && <HospitalWorkspace key={actor} />}
  </main>;
}
createRoot(document.getElementById('root')!).render(<Sandbox />);
