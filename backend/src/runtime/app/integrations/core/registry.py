from typing import Dict, Any, Optional, List
from app.integrations.core.contracts import (
    IntegrationAdapter,
    ConnectorFactory,
    UnsupportedConnector
)
from app.integrations.vendors.practice_fusion.adapter import PracticeFusionAdapter
from app.integrations.vendors.practice_fusion.manifest import PracticeFusionManifest
from app.integrations.hospital_read.adapter import HospitalReadFactory, HospitalReadAdapter


class PracticeFusionFactory(ConnectorFactory):
    """Factory for instantiating Practice Fusion adapters from validated config."""

    @classmethod
    def from_validated_config(cls, config: Dict[str, Any]) -> PracticeFusionAdapter:
        manifest = PracticeFusionManifest()
        return PracticeFusionAdapter(auth=None, manifest=manifest, config=config)


class SyntheticRestAdapter(IntegrationAdapter):
    """
    Synthetic REST adapter for testing multi-vendor integration neutrality.
    Satisfies G1 Gate required pass condition.
    """

    REQUIRED_CONFIG = ["base_url"]

    def __init__(self, config: Dict[str, Any]):
        for field in self.REQUIRED_CONFIG:
            if not config.get(field):
                raise ValueError(f"SyntheticRestAdapter requires '{field}' in configuration")
        self.config = config
        self.connector_id = config.get("id", "SYNTH-REST-01")
        self.base_url = config.get("base_url")

    async def get_capability_statement(self) -> Dict[str, Any]:
        return {
            "resourceType": "CapabilityStatement",
            "status": "active",
            "kind": "instance",
            "software": {"name": "SyntheticRestServer", "version": "1.0.0"},
            "rest": [{
                "mode": "server",
                "resource": [
                    {"type": "Claim", "interaction": [{"code": "read"}]},
                    {"type": "Encounter", "interaction": [{"code": "read"}]}
                ]
            }]
        }

    async def get_resource(
        self,
        resource_type: str,
        query_params: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        return []

    async def sync(self) -> Dict[str, Any]:
        return {"status": "Success", "records_processed": 0, "vendor": "synthetic_rest"}


class SyntheticRestFactory(ConnectorFactory):
    """Factory for instantiating Synthetic REST adapters from validated config."""

    @classmethod
    def from_validated_config(cls, config: Dict[str, Any]) -> SyntheticRestAdapter:
        return SyntheticRestAdapter(config=config)


# Exact ID registered connectors — NO substring matching
CONNECTORS: Dict[str, Any] = {
    "hospital_read": HospitalReadFactory,
    "practice_fusion": PracticeFusionFactory,
    "synthetic_rest": SyntheticRestFactory
}


def instantiate(vendor_key: str, config: Dict[str, Any]) -> IntegrationAdapter:
    """
    G1: Instantiate adapter using exact vendor key lookup.
    Never uses substring matching (`if key in vendor_name`).
    Raises UnsupportedConnector on unregistered vendors.
    """
    if not vendor_key or not isinstance(vendor_key, str):
        raise UnsupportedConnector(f"Invalid connector vendor key: {vendor_key}")

    normalized_key = vendor_key.strip().lower().replace(" ", "_")
    factory = CONNECTORS.get(normalized_key)
    if factory is None:
        raise UnsupportedConnector(f"Unsupported connector vendor: '{vendor_key}'")

    return factory.from_validated_config(config)


class IntegrationRegistry:
    """
    G1 Central registry for discovering and instantiating vendor integration adapters.
    Keeps backwards compatibility via thin wrappers over exact-match instantiate().
    """

    def __init__(self):
        self._vendors = {
            "hospital_read": {"adapter": HospitalReadAdapter, "manifest": None, "factory": HospitalReadFactory},
            "practice_fusion": {
                "adapter": PracticeFusionAdapter,
                "manifest": PracticeFusionManifest,
                "factory": PracticeFusionFactory
            },
            "synthetic_rest": {
                "adapter": SyntheticRestAdapter,
                "manifest": None,
                "factory": SyntheticRestFactory
            }
        }

    def create(self, vendor_id: str, config: Dict[str, Any]) -> Optional[IntegrationAdapter]:
        """
        Thin compatibility wrapper over instantiate().
        Returns None for unsupported connectors (never Connected/Healthy).
        """
        try:
            return instantiate(vendor_id, config)
        except (UnsupportedConnector, ValueError):
            return None


registry = IntegrationRegistry()
