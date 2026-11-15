from typing import Dict, Any, List, Optional, Tuple
from pydantic import BaseModel, Field


class SemanticMappingDefinition(BaseModel):
    """
    G1/G3: Declarative source semantic mapping definition.
    Ensures ambiguous or unmapped statuses fail closed rather than guessing.
    """
    mapping_id: str
    version: str = "1.0"
    source_field: str
    source_value: Any
    canonical_field: str = ""
    canonical_value: Any = None
    requires: List[str] = Field(default_factory=list)
    when_incomplete: str = "STATUS_UNRESOLVED"
    when_unmapped: str = "NOT_EVALUABLE"


class MappingRegistry:
    """
    G1 Central registry for source semantic mappings.
    Stores and looks up versioned mappings for EHR/Billing/Claims fields.
    """

    def __init__(self):
        self._mappings: Dict[Tuple[str, str, Any], SemanticMappingDefinition] = {}

    def register_mapping(self, definition: SemanticMappingDefinition) -> None:
        key = (definition.mapping_id, definition.version, definition.source_value)
        self._mappings[key] = definition

    def get_mapping(
        self,
        mapping_id: str,
        version: str,
        source_value: Any
    ) -> Optional[SemanticMappingDefinition]:
        return self._mappings.get((mapping_id, version, source_value))

    def evaluate_mapping(
        self,
        mapping_id: str,
        version: str,
        source_value: Any,
        context: Dict[str, Any]
    ) -> Tuple[str, Any]:
        """
        Evaluate semantic mapping:
        - If mapped and required fields present -> (SUCCESS, canonical_value)
        - If mapped but required fields missing -> (STATUS_UNRESOLVED, None)
        - If not mapped -> (NOT_EVALUABLE, None)
        """
        definition = self.get_mapping(mapping_id, version, source_value)
        if not definition:
            return "NOT_EVALUABLE", None

        # Check required context fields
        for req in definition.requires:
            if not context.get(req):
                return definition.when_incomplete, None

        return "SUCCESS", definition.canonical_value


mapping_registry = MappingRegistry()
