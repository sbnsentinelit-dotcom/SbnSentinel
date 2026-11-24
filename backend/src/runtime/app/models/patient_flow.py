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

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, UniqueConstraint

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
NO_SHOW = "NO_SHOW"                # booked visit recorded as a no-show (the patient never arrived)
# Waiting-room evidence (who is still waiting, how long since ready, who follows up):
ROOMED = "ROOMED"                  # patient placed in a room; this is NOT the consultation start
FILE_READY = "FILE_READY"          # medical records: the patient's file is ready
FILE_REVOKED = "FILE_REVOKED"
NURSING_DONE = "NURSING_DONE"      # nursing assessment complete
NURSING_REVOKED = "NURSING_REVOKED"
PAYER_APPROVED = "PAYER_APPROVED"  # payer authorization approved (only when the service requires it)
PAYER_REVOKED = "PAYER_REVOKED"
HANDOFF_SENT = "HANDOFF_SENT"      # handoff to the receiving team sent (handoff_id, destination)
HANDOFF_ACCEPTED = "HANDOFF_ACCEPTED"  # the receiving team accepted that handoff

VISIT_EVENT_TYPES = (ARRIVED, READY, CONSULT_START, CONSULT_END, LAB_START, LAB_END, BILLING,
                     CHECKED_OUT, CANCELLED, NO_SHOW, ROOMED, FILE_READY, FILE_REVOKED, NURSING_DONE,
                     NURSING_REVOKED, PAYER_APPROVED, PAYER_REVOKED, HANDOFF_SENT, HANDOFF_ACCEPTED)
TERMINAL_EVENTS = (CHECKED_OUT, CANCELLED)

# The team that is authorized to record each event (an event from another source is not trusted).
SOURCE_ROLE_OF_EVENT = {
    ARRIVED: "reception", CANCELLED: "reception", NO_SHOW: "reception", CHECKED_OUT: "reception",
    CONSULT_START: "clinician", CONSULT_END: "clinician", ROOMED: "rooms",
    FILE_READY: "records", FILE_REVOKED: "records", NURSING_DONE: "nursing", NURSING_REVOKED: "nursing",
    PAYER_APPROVED: "payer", PAYER_REVOKED: "payer", HANDOFF_SENT: "nursing", HANDOFF_ACCEPTED: "consult_team",
    READY: "nursing", LAB_START: "lab", LAB_END: "lab", BILLING: "billing",
}

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
    # When Sentinel learned about the event (a late entry is not counted before it was known); UTC.
    known_at = Column(DateTime, nullable=True)
    source_role = Column(String(32), nullable=True)       # reception, nursing, records, payer, clinician, ...
    handoff_id = Column(String(64), nullable=True)
    destination = Column(String(64), nullable=True)       # receiving team of a handoff
    payer_required = Column(Boolean, nullable=True)       # on ARRIVED: does this service need payer approval?
    # A data correction never deletes an event: it is superseded (from this time on it no longer counts).
    superseded_at = Column(DateTime, nullable=True)
    superseded_by = Column(String(160), nullable=True)


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
