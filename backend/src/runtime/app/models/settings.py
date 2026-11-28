from sqlalchemy import Column, Index, Integer, String, Boolean, text
from app.db.database import Base


class SettingsModel(Base):
    __tablename__ = "settings"
    # PR #8 follow-up: the first open of a scope from two places at once still yields one row.
    __table_args__ = (
        Index("uq_settings_org_scope", "org_id", unique=True,
              sqlite_where=text("clinic_id IS NULL AND org_id IS NOT NULL"),
              postgresql_where=text("clinic_id IS NULL AND org_id IS NOT NULL")),
        Index("uq_settings_clinic_scope", "org_id", "clinic_id", unique=True,
              sqlite_where=text("clinic_id IS NOT NULL"), postgresql_where=text("clinic_id IS NOT NULL")),
    )

    id = Column(Integer, primary_key=True, index=True)
    # Owner of this configuration (MS-001): one row per organization (clinic_id empty) and,
    # where a clinic keeps its own practice details, one row per clinic.
    org_id = Column(String, index=True, nullable=True)
    clinic_id = Column(String, index=True, nullable=True)
    practice_name = Column(String, default="Sentinel Health Urgent Care")
    practice_phone = Column(String, default="(555) 019-2834")
    timezone = Column(String, default="Eastern Time (US & Canada)")
    open_time = Column(String, default="08:00")
    close_time = Column(String, default="20:00")
    language = Column(String, default="en")
    theme_mode = Column(String, default="system")

    # SESR-012C / CCA-023 Governed Organizational Configuration
    # AI Settings (Must be governed by Policy/Rule Engine for deterministic behavior)
    # These fields act as mapped proxies to the authoritative Rule Governance framework.
    scheduling_aggressiveness = Column(Integer, default=2)
    auto_outreach = Column(Boolean, default=True)
    confidence_threshold = Column(String, default="85% (Recommended)")
    ai_model = Column(String, default="gpt-4o")

    # Notifications
    notify_sms = Column(Boolean, default=True)
    notify_email = Column(Boolean, default=False)
    notify_desktop = Column(Boolean, default=True)
    notify_copay = Column(Boolean, default=True)
    reminder_interval = Column(String, default="24h")

    # Billing
    active_plan = Column(String, default="professional")
    payment_card = Column(String, default="Visa ending in 4242")
