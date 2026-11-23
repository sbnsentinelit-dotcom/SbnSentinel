"""Clinic patient-flow policy: the values the Patient Flow calculation uses (one place, versioned)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


@dataclass(frozen=True)
class PatientFlowPolicy:
    policy_id: str = "POL-PATIENT-FLOW"
    version: str = "1"
    utc_offset_minutes: int = 240          # clinic time zone: UAE (UTC+4, no daylight saving)
    timezone_label: str = "UAE"
    delay_threshold_minutes: int = 30      # waiting longer than this since arrival = Delayed
    cleaning_minutes: int = 10             # a room is cleaned for this long after a consultation
    consult_slot_minutes: int = 20         # a consultation longer than this is running late
    open_hour: int = 8                     # clinic hours (local)
    close_hour: int = 20
    feed_fresh_minutes: int = 5            # the queue is "synced" if an event arrived this recently

    @property
    def offset(self) -> timedelta:
        return timedelta(minutes=self.utc_offset_minutes)

    def to_local(self, utc_dt: datetime) -> datetime:
        return utc_dt + self.offset

    def local_date(self, utc_dt: datetime) -> date:
        return self.to_local(utc_dt).date()


DEFAULT_FLOW_POLICY = PatientFlowPolicy()


def local_midnight_utc(day: date, policy: PatientFlowPolicy = DEFAULT_FLOW_POLICY) -> datetime:
    """UTC instant of the clinic's local midnight that starts `day`."""
    return datetime.combine(day, time(0, 0)) - policy.offset
