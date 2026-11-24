import asyncio
import random
from datetime import datetime, timezone
from app.schemas.signal import SignalEvent

# SES-002: All simulation data flows through the event pipeline
from app.services.processing_orchestrator import processing_orchestrator

DEMO_ORG_ID = "ORG-UAE-01"
DEMO_CLINIC_ID = "DXB-CLINIC-01"


# Denied claims sent through the pipeline right away when the demo starts, so the queue is not empty.
CLAIM_BACKLOG = 20
# Seconds between live active signals (no-show, long wait, claim denial): about one every 50 seconds.
ACTIONABLE_INTERVAL = (45, 55)


def _spread(key: str, choices):
    """Deterministic choice per key, so a claim always gets the same synthetic payer data."""
    import hashlib
    return choices[int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(choices)]


# Days until the payer resubmission deadline, as a payer / clearinghouse would report it.
# None = the payer has not sent a deadline yet (evidence incomplete).
_DEADLINE_DAYS = (0.5, 1.5, 3, 5, 6, 10, 18, 30, None, -1)


def synthetic_claim_risk_inputs(claim, code, denied_amount, currency, now=None):
    """Remittance data a connected payer / clearinghouse sends with a claim denial, plus the evidence
    the EHR already holds (synthetic here)."""
    from datetime import timedelta
    from app.services.uae_claims_governance import SYNTHETIC_REQUIRED_EVIDENCE
    now = now or datetime.utcnow()
    days = _spread(claim.claim_id, _DEADLINE_DAYS)
    remittance = getattr(claim, "adjudication_date", None) or now
    required = list(SYNTHETIC_REQUIRED_EVIDENCE.get(code, []))
    # Most evidence is already in the EHR; for about one claim in three the last item is not.
    available = required if _spread(claim.claim_id + "-ehr", (True, True, False)) else required[:-1]
    return {
        "source": "Payer remittance + EHR (synthetic)",
        "claim_id": claim.claim_id,
        "payer": claim.payer_id,
        "denial_reason": claim.rejection_reason,
        "required_evidence": required,
        "evidence_available": available,
        "denial_code": code,
        "denied_amount": f"{denied_amount:.2f}",
        "currency": currency,
        "remittance_date": remittance.isoformat() + "Z",
        "resubmission_deadline": (now + timedelta(days=days)).isoformat() + "Z" if days is not None else None,
    }


def synthetic_ehr_risk_inputs(status, rng=random):
    """Appointment data a connected EHR sends with a no-show or a long wait (synthetic here)."""
    if status == "NO_SHOW":
        return {
            "source": "EHR appointment (synthetic)",
            "appointment_at": datetime.utcnow().isoformat() + "Z",
            "visit_priority": rng.choice(["Routine", "Routine", "Routine", "Follow-up", "Follow-up", "Urgent"]),
            "prior_no_shows_90d": rng.choice([0, 0, 1, 1, 2, 3]),
            "visit_value": f"{rng.choice([180, 250, 320, 450, 600, 850]):.2f}",
            "currency": "AED",
        }
    if status == "WAIT_TIME_EXCEEDED":
        return {
            "source": "EHR front desk (synthetic)",
            "wait_minutes": rng.choice([32, 38, 41, 45, 52, 58, 64]),
            "patient_priority": rng.choice(["Normal", "Normal", "Normal", "Urgent"]),
            "room_available": rng.choice([True, True, False]),
        }
    return None


def live_simulation_enabled() -> bool:
    """Whether the live signal feed may run: its own opt-in, synthetic mode, outside PRODUCTION."""
    from app.core.config import settings
    return (bool(getattr(settings, "ENABLE_LIVE_SIMULATION", False))
            and bool(getattr(settings, "SYNTHETIC_TEST_ENABLED", False))
            and getattr(settings, "ENVIRONMENT", "").upper() != "PRODUCTION")


def _random_workbook_patient() -> dict:
    from app.services import workbook_dataset
    rows = [p for p in workbook_dataset.patients() if p.get("patient_ref")]
    return random.choice(rows) if rows else {}


def _workbook_patient_name(patient_ref):
    from app.services import workbook_dataset
    return (workbook_dataset.patients_by_ref().get(patient_ref) or {}).get("name") or None


class SimulationEngine:
    def __init__(self):
        self.running = False
        self.visit_generator_running = False
        self.clients = []
        self._used_claim_ids = None

        # Patients come from the clinic's workbook (name and patient ID), not from a fixed list.
        # "status" is the typed appointment status a real EHR connector sends. The evidence engine
        # never infers a no-show or a long wait from message text, so without it these events can
        # never match the scheduling rules. Every event here leads to a recommendation and comes on
        # the ACTIONABLE_INTERVAL schedule (about one every 50 seconds).
        self.events = [
            # The clinic's EHR feed (generic source; vendor names are never shown to the clinic).
            {"source": "EHR", "type": "EHR", "msg_template": "Patient {patient} appointment marked as No-Show.",
             "status": "NO_SHOW", "weight": 1, "actionable": True},
            {"source": "EHR", "type": "EHR", "msg_template": "Patient {patient} wait time has reached {wait} minutes.",
             "status": "WAIT_TIME_EXCEEDED", "weight": 1, "actionable": True},
            {"claim_denial": True, "weight": 1, "actionable": True},
        ]

    def _next_denied_claim(self):
        """A seeded denied claim with a governed UAE denial code that has no signal yet."""
        import json
        from app.db.database import SessionLocal
        from app.models.financial_reporting import FinancialClaimModel
        from app.models.signal import SignalModel
        from app.services.uae_claims_governance import UAE_DENIAL_RULES
        db = SessionLocal()
        try:
            if self._used_claim_ids is None:
                self._used_claim_ids = set()
                for (meta,) in db.query(SignalModel.metadata_data).all():
                    data = meta if isinstance(meta, dict) else (json.loads(meta) if meta else {})
                    if data.get("claim_id"):
                        self._used_claim_ids.add(data["claim_id"])
            candidates = (
                db.query(FinancialClaimModel)
                .filter(FinancialClaimModel.status == "denied",
                        FinancialClaimModel.clinic_id == DEMO_CLINIC_ID,
                        FinancialClaimModel.denial_code.in_(list(UAE_DENIAL_RULES)))
                .all()
            )
            remaining = [c for c in candidates if c.claim_id not in self._used_claim_ids]
            if not remaining:
                return None
            claim = random.choice(remaining)
            self._used_claim_ids.add(claim.claim_id)
            db.expunge(claim)
            return claim
        finally:
            db.close()

    def _claim_event(self):
        """Builds a payer claim-denial event (typed remittance facts) for a seeded denied claim."""
        from app.services.dubai_seed_service import synthetic_patient_name
        from app.services.uae_claims_governance import UAE_DENIAL_RULES, UAE_CLAIMS_SOURCE
        claim = self._next_denied_claim()
        if claim is None:
            return None
        code = claim.denial_code
        denied_amount = round((claim.billed_amount or 0.0) - (claim.paid_amount or 0.0), 2)
        currency = claim.currency or "AED"
        patient = _workbook_patient_name(claim.patient_id) or synthetic_patient_name(claim.patient_id)
        title = UAE_DENIAL_RULES[code][0]
        message = f"[{code}] {title}: Exposure {currency} {denied_amount:,.2f}"
        facts = [
            {"entity": "Claim", "key": "denial_code", "value": code, "source": UAE_CLAIMS_SOURCE},
            {"entity": "Claim", "key": "claim_id", "value": claim.claim_id, "source": UAE_CLAIMS_SOURCE},
            {"entity": "Claim", "key": "denied_amount", "value": f"{denied_amount:.2f}", "source": UAE_CLAIMS_SOURCE},
            {"entity": "Claim", "key": "currency", "value": currency, "source": UAE_CLAIMS_SOURCE},
        ]
        extra = {"claim_id": claim.claim_id, "payer": claim.payer_id, "patient_id": claim.patient_id,
                 "denial_reason": claim.rejection_reason,
                 "risk_inputs": synthetic_claim_risk_inputs(claim, code, denied_amount, currency)}
        return UAE_CLAIMS_SOURCE, "Claim", message, patient, facts, extra

    async def _ingest(self, source, event_type, message_str, patient, facts=None, extra=None):
        # ─────────────────────────────────────────────────────────────
        # SES-002 COMPLIANT: All data flows through the 8-layer pipeline
        # No direct engine calls — pipeline orchestrator handles all layers
        # ─────────────────────────────────────────────────────────────
        from app.db.database import SessionLocal
        from app.models.event import OperationalEventModel
        from app.models.intelligence import OperationalIntelligenceModel, RevenueIntelligenceModel, DecisionContextModel

        event_model = processing_orchestrator.create_event(
            event_type=event_type,
            source=source,
            raw_payload={
                "patient_name": patient,
                "detail": message_str,
                "content": message_str,
                # Synthetic events belong to the demo organization so that scoped
                # signal lists show them to that organization's users.
                "org_id": DEMO_ORG_ID,
                "clinic_id": DEMO_CLINIC_ID,
                **({"facts": facts} if facts else {}),
                **(extra or {}),
            },
            priority=random.choice(["Normal", "Normal", "High", "Low", "Critical"]),
            initiated_by="system@sentinel.local",
        )

        # The signal created for this event has the event's ID; recommendations target that signal,
        # so actions on them can be scoped to the signal's organization and clinic.
        db = SessionLocal()
        try:
            stored = db.query(OperationalEventModel).filter(OperationalEventModel.id == event_model.id).first()
            if stored is not None:
                stored.raw_payload = {**(stored.raw_payload or {}), "target_reference": stored.id, "target_type": "Signal"}
                db.commit()
        finally:
            db.close()

        # Execute pipeline synchronously for simulation feed
        processing_orchestrator.process_event_background(event_model.id)

        db = SessionLocal()
        try:
            intel = db.query(OperationalIntelligenceModel).filter(
                OperationalIntelligenceModel.event_id == event_model.id).first()
            revenue = db.query(RevenueIntelligenceModel).filter(
                RevenueIntelligenceModel.event_id == event_model.id).first()
            context = db.query(DecisionContextModel).filter(
                DecisionContextModel.event_id == event_model.id).first()
            from app.models.signal import SignalModel
            from app.services.risk_level_evaluator import current_evaluation
            stored_signal = db.query(SignalModel).filter(SignalModel.id == event_model.id).first()

            event = SignalEvent(
                id=event_model.id,
                source=source,
                type=event_type,
                message=message_str,
                timestamp=datetime.now(timezone.utc).isoformat(),
                metadata=(stored_signal.metadata_data if stored_signal and stored_signal.metadata_data else
                          {"patient_name": patient, "pipeline_event_id": event_model.id,
                           "org_id": DEMO_ORG_ID, "clinic_id": DEMO_CLINIC_ID, **(extra or {})}),
                risk_level=(stored_signal.risk_level if stored_signal else None)
                or (intel.priority if intel else "Information"),
                risk_evaluation=current_evaluation(stored_signal.metadata_data) if stored_signal else None,
                problem=(stored_signal.problem if stored_signal else None) or "",
                reason=(stored_signal.reason if stored_signal else None) or "",
                business_impact=intel.operational_impact if intel else "",
                recommended_action=(stored_signal.recommended_action if stored_signal else None)
                or (intel.recommendation if intel else ""),
                expected_outcome=(stored_signal.expected_outcome if stored_signal else None) or "",
                primary_context=context.primary_context if context else "",
                secondary_context=context.secondary_context if context else "",
                context_confidence=(context.sufficiency_status or "") if context else "",
                context_reason=context.reason if context else "",
                revenue_risk_category=revenue.opportunity_category if revenue else "UNAVAILABLE",
                estimated_financial_exposure=revenue.estimated_exposure if revenue else "UNAVAILABLE",
                revenue_confidence=revenue.financial_priority if revenue else "UNAVAILABLE",
                operational_dependency=""
            )
            await self.broadcast(event)
        finally:
            db.close()

    async def _ingest_claim_backlog(self):
        """Sends a first batch of denied claims through the pipeline when few claim signals exist."""
        import logging
        from app.db.database import SessionLocal
        from app.models.signal import SignalModel
        from app.services.uae_claims_governance import UAE_CLAIMS_SOURCE
        db = SessionLocal()
        try:
            existing = db.query(SignalModel).filter(SignalModel.source == UAE_CLAIMS_SOURCE).count()
        finally:
            db.close()
        for _ in range(max(0, CLAIM_BACKLOG - existing)):
            if not self.running:
                return
            try:
                built = self._claim_event()
                if built is None:
                    return
                await self._ingest(*built)
            except Exception as e:
                logging.getLogger(__name__).error(f"[SimEngine] Claim backlog error: {e}")
            await asyncio.sleep(0.2)

    async def generate_realistic_data(self):
        # A first batch of denied claims, then one active signal about every 50 seconds.
        await self._ingest_claim_backlog()
        await self.generate_actionable_data()

    async def generate_actionable_data(self):
        actionable = [e for e in self.events if e.get("actionable")]
        while self.running:
            await asyncio.sleep(random.uniform(*ACTIONABLE_INTERVAL))
            await self._ingest_template(random.choices(actionable, weights=[e["weight"] for e in actionable])[0])

    async def _ingest_template(self, event_template):
        try:
            if event_template.get("claim_denial"):
                built = self._claim_event()
                if built is not None:
                    await self._ingest(*built)
                return

            row = _random_workbook_patient()
            patient = row.get("name") or row.get("patient_ref") or "Unknown patient"
            facts = ([{"entity": "Appointment", "key": "status", "value": event_template["status"],
                       "source": event_template["source"]}] if event_template.get("status") else None)
            risk_inputs = synthetic_ehr_risk_inputs(event_template.get("status"))
            message = event_template["msg_template"].format(
                patient=patient, wait=(risk_inputs or {}).get("wait_minutes", ""))
            extra = {"patient_ref": row.get("patient_ref"), "mrn": row.get("mrn")}
            if risk_inputs:
                extra["risk_inputs"] = risk_inputs
            await self._ingest(event_template["source"], event_template["type"], message, patient, facts, extra)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"[SimEngine] Pipeline error: {e}")

    async def generate_dynamic_encounters(self):
        """Plays the clinic's queue feed while none is connected: stores the synthetic visit events
        whose time has passed (services/visit_generator.py). It writes events only; the Patient Flow
        screen calculates every status, wait and count from them."""
        from app.db.database import SessionLocal
        from app.models.clinic import ClinicModel
        from app.services.visit_generator import sync_generated_events
        while self.visit_generator_running:
            await asyncio.sleep(20)
            db = SessionLocal()
            try:
                for (clinic_id,) in db.query(ClinicModel.id).all():
                    sync_generated_events(db, clinic_id, days=1)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"[Visit generator] Error: {e}")
            finally:
                db.close()

    async def broadcast(self, event: SignalEvent):
        # Push to all connected WebSocket clients
        for q in self.clients:
            await q.put(event.model_dump_json())

    def start(self):
        # The live signal feed has its own opt-in (Report 01 §01.1): synthetic mode and
        # ENABLE_LIVE_SIMULATION, never in PRODUCTION. It needs no vendor connector.
        if not live_simulation_enabled():
            return
        if not self.running:
            self.running = True
            asyncio.create_task(self.generate_realistic_data())

    def start_visit_generator(self):
        """The synthetic visit generator (Patient Flow queue feed) runs in synthetic mode outside
        production, independent of the signal simulator and of any vendor connector."""
        from app.core.config import settings
        if (not getattr(settings, "SYNTHETIC_TEST_ENABLED", False)
                or getattr(settings, "ENVIRONMENT", "").upper() == "PRODUCTION"):
            return
        if not self.visit_generator_running:
            self.visit_generator_running = True
            asyncio.create_task(self.generate_dynamic_encounters())

    def stop(self):
        self.running = False
        self.visit_generator_running = False

    def add_client(self, queue: asyncio.Queue):
        self.clients.append(queue)

    def remove_client(self, queue: asyncio.Queue):
        if queue in self.clients:
            self.clients.remove(queue)


simulation_engine = SimulationEngine()
