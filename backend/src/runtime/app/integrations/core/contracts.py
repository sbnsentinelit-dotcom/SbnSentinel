from abc import ABC, abstractmethod
from typing import Dict, Any, List


class UnsupportedConnector(Exception):
    """Raised when an unregistered or unsupported connector vendor ID is requested."""
    pass


class IntegrationAdapter(ABC):
    """
    Base contract for all external integration adapters.
    Adapters are thin wrappers mapping generic framework calls
    to vendor-specific endpoints and behaviors.
    """

    @abstractmethod
    async def get_capability_statement(self) -> Dict[str, Any]:
        """Fetch vendor capability metadata."""

    @abstractmethod
    async def get_resource(
        self,
        resource_type: str,
        query_params: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """Generic resource fetch."""

    async def sync(self) -> Dict[str, Any]:
        """Default sync implementation for lifecycle management."""
        return {"status": "Success", "details": "Sync completed"}


class ConnectorFactory(ABC):
    """
    Base contract for vendor connector factories.
    """

    @classmethod
    @abstractmethod
    def from_validated_config(cls, config: Dict[str, Any]) -> IntegrationAdapter:
        """Instantiate an adapter from validated configuration."""
        pass


class AuthStrategy(ABC):
    """
    Base contract for all authentication strategies.
    """

    @abstractmethod
    async def authenticate(self) -> Dict[str, Any]:
        """Authenticate and return credentials/tokens."""
