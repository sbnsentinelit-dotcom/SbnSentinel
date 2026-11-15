from datetime import datetime
from typing import Dict, Any, Optional, Tuple, Set
from pydantic import BaseModel, Field


class CapabilityGrant(BaseModel):
    """
    G4 Capability Grant.
    Represents technical and contractual readiness for a single capability.
    Enables capability-driven readiness queries (e.g. claim.read without Patient endpoint).
    """
    connector_instance_id: str
    capability: str  # e.g. "claim.read", "encounter.read", "patient.read"
    transport_ready: bool = False
    permission_verified: bool = False
    data_contract_verified: bool = False
    last_checked_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def is_executable(self, source_evidence_is_sufficient: bool = True) -> bool:
        """
        G4 Evaluation readiness rule:
        can_evaluate = all([grant.transport_ready, grant.permission_verified, grant.data_contract_verified, source_evidence_is_sufficient])
        """
        return all([
            self.transport_ready,
            self.permission_verified,
            self.data_contract_verified,
            source_evidence_is_sufficient,
        ])


class CapabilityRegistry:
    """
    Authoritative registry for connector capability grants.
    Maintains per-connector, per-capability grants and evaluates readiness.
    """

    def __init__(self):
        self._grants: Dict[Tuple[str, str], CapabilityGrant] = {}

    def register_grant(self, grant: CapabilityGrant) -> None:
        key = (grant.connector_instance_id, grant.capability.lower())
        self._grants[key] = grant

    def get_grant(self, connector_instance_id: str, capability: str) -> Optional[CapabilityGrant]:
        key = (connector_instance_id, capability.lower())
        return self._grants.get(key)

    def is_capability_ready(
        self,
        connector_instance_id: str,
        capability: str,
        source_evidence_is_sufficient: bool = True,
    ) -> bool:
        """
        Query readiness per capability, NOT caps.supports_search("Patient").
        Fails closed if grant is missing or incomplete.
        """
        grant = self.get_grant(connector_instance_id, capability)
        if not grant:
            return False
        return grant.is_executable(source_evidence_is_sufficient=source_evidence_is_sufficient)

    def list_capabilities(self, connector_instance_id: str) -> Set[str]:
        return {
            cap for (cid, cap) in self._grants.keys()
            if cid == connector_instance_id
        }

    def clear(self) -> None:
        self._grants.clear()


capability_registry = CapabilityRegistry()
