"""
Patient Flow storage.

visit_events is the source of truth for the Live Patient Flow screen: every number on the screen
(waiting now, in consultation now, average wait, delayed, trends, rooms, suggestions) is calculated
from these events. They come from a source system (an EHR / queue feed), from a person on the
screen (manual reports, recorded with the actor), or, while no real feed is connected, from the
synthetic visit generator. The calculation is the same for all of them.

clinic_rooms holds the clinic's rooms; their state is calculated from the visit events.
patient_flow_actions records every staff action taken from the screen (notifications, calls,
dismissed suggestions, manual status reports) with its connector receipt.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, JSON, String, UniqueConstraint

from app.db.database import Base


# Event types a visit goes through. A source maps its own statuses onto these.
ARRIVED = "ARRIVED"                # patient checked in at the front desk
READY = "READY"                    # registration / triage done, waiting for the provider
CONSULT_START = "CONSULT_START"    # provider started the consultation (in a room)
CONSULT_END = "CONSULT_END"
LAB_START = "LAB_START"
LAB_END = "LAB_END"
BILLING = "BILLING"                # at the billing desk
CHECKED_OUT = "CHECKED_OUT"        # left the clinic: visit complete
CANCELLED = "CANCELLED"            # left without being seen / cancelled after arrival
ROOM_STATUS = "ROOM_STATUS"        # a person set a room's status (detail = Available / Cleaning / Occupied)

VISIT_EVENT_TYPES = (ARRIVED, READY, CONSULT_START, CONSULT_END, LAB_START, LAB_END, BILLING,
                     CHECKED_OUT, CANCELLED)
TERMINAL_EVENTS = (CHECKED_OUT, CANCELLED)

SOURCE_GENERATOR = "visit_generator"
SOURCE_MANUAL = "manual"


class VisitEventModel(Base):
    __tablename__ = "visit_events"
    __table_args__ = (UniqueConstraint("event_key", name="uq_visit_events_event_key"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Idempotency key: the source's own event id. Delivering the same event twice stores it once.
    event_key = Column(String(160), nullable=False, index=True)
    clinic_id = Column(String(64), nullable=False, index=True)
    visit_id = Column(String(64), nullable=True, index=True)
    event_type = Column(String(32), nullable=False)
    event_time = Column(DateTime, nullable=False, index=True)  # UTC
    patient_ref = Column(String(64), nullable=True)
    patient_name = Column(String(160), nullable=True)
    provider = Column(String(160), nullable=True)
    department = Column(String(120), nullable=True)
    priority = Column(String(32), nullable=True)
    room = Column(String(64), nullable=True)
    detail = Column(String(255), nullable=True)
    source = Column(String(64), nullable=False)
    recorded_by = Column(String(160), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ClinicRoomModel(Base):
    __tablename__ = "clinic_rooms"
    __table_args__ = (UniqueConstraint("clinic_id", "name", name="uq_clinic_rooms_clinic_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    clinic_id = Column(String(64), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    kind = Column(String(16), nullable=False, default="consult")  # consult | lab
    doctor = Column(String(160), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class PatientFlowActionModel(Base):
    __tablename__ = "patient_flow_actions"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_patient_flow_actions_key"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    action_id = Column(String(40), nullable=False, index=True)
    idempotency_key = Column(String(160), nullable=False)
    clinic_id = Column(String(64), nullable=False, index=True)
    action_type = Column(String(40), nullable=False)
    visit_id = Column(String(64), nullable=True, index=True)
    suggestion_id = Column(String(160), nullable=True, index=True)
    recipient = Column(String(160), nullable=True)
    message = Column(String(500), nullable=True)
    basis = Column(JSON, nullable=True)       # what the action was based on (suggestion facts, as-of time)
    status = Column(String(32), nullable=False)
    connector = Column(String(80), nullable=True)
    receipt_ref = Column(String(80), nullable=True)
    detail = Column(String(255), nullable=True)
    actor = Column(String(160), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
