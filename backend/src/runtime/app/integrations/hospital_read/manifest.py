"""Reviewed source profile. Configuration does not establish source readiness."""
from dataclasses import dataclass, field
from types import MappingProxyType
from urllib.parse import urlsplit
from collections.abc import Mapping

RESOURCES = frozenset({"Claim", "ClaimLine", "SubmissionAttempt", "Remittance", "Receipt"})
SEMANTICS = {
    "Claim": {"draft", "submitted", "rejected_technical", "adjudicated", "paid", "denied"},
    "ClaimLine": {"submitted", "paid", "partially_paid", "denied"},
    "SubmissionAttempt": {"accepted", "rejected_technical", "disputed"},
    "Remittance": {"advised", "received"}, "Receipt": {"received"},
}


@dataclass(frozen=True)
class HospitalReadProfile:
    revision: str
    org_id: str
    clinic_id: str
    connector_id: str
    base_url: str
    endpoints: dict[str, str]
    mapping_id: str
    mapping_version: str
    status_map: dict[str, dict[str, str]]
    max_rows: int = 500
    max_bytes: int = 2 * 1024 * 1024
    max_pages_per_run: int = 1000
    cohort_hash: str = ""
    business_timezone: str = "Asia/Dubai"
    resync_basis_ref: str | None = None
    department_map: dict[str, str] = field(default_factory=dict)
    department_ids_are_canonical: bool = False

    def __post_init__(self):
        if (not isinstance(self.endpoints, Mapping) or not isinstance(self.status_map, Mapping)
                or not isinstance(self.department_map, Mapping) or type(self.department_ids_are_canonical) is not bool):
            raise ValueError("Invalid profile mapping structure")
        for value in (self.max_rows, self.max_bytes, self.max_pages_per_run):
            if type(value) is not int:
                raise ValueError("Profile limits must be integers")
        if any(not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip()
               for key, value in self.department_map.items()):
            raise ValueError("Explicit source department crosswalk required")
        for value in (self.revision, self.org_id, self.clinic_id, self.connector_id,
                      self.mapping_id, self.mapping_version, self.cohort_hash):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Profile identity, mapping and cohort must be explicit")
        url = urlsplit(self.base_url)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in {"", "/"}):
            raise ValueError("Reviewed HTTPS origin required")
        _ = url.port  # reject malformed authority/port during configuration
        if not 1 <= self.max_rows <= 500 or not 1 <= self.max_bytes <= 2 * 1024 * 1024:
            raise ValueError("Invalid bounded profile limits")
        if not 1 <= self.max_pages_per_run <= 10000:
            raise ValueError("Invalid page-window bound")
        if not self.endpoints or not set(self.endpoints).issubset(RESOURCES):
            raise ValueError("Unsupported source resources")
        if not set(self.status_map).issubset(self.endpoints):
            raise ValueError("Status mappings reference unavailable resources")
        for kind, mapping in self.status_map.items():
            if not isinstance(mapping, Mapping):
                raise ValueError("Invalid status mapping")
            if not set(mapping.values()).issubset(SEMANTICS[kind]):
                raise ValueError("Invalid canonical status for resource")
        for path in self.endpoints.values():
            if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or ".." in path or "?" in path or "#" in path:
                raise ValueError("Reviewed absolute endpoint paths required")
        object.__setattr__(self, "endpoints", MappingProxyType(dict(self.endpoints)))
        object.__setattr__(self, "department_map", MappingProxyType(dict(self.department_map)))
        object.__setattr__(self, "status_map", MappingProxyType({
            kind: MappingProxyType(dict(mapping)) for kind, mapping in self.status_map.items()}))

    def cursor_binding(self, resource_kind, transport="rest"):
        import hashlib, json
        if resource_kind not in self.endpoints:
            raise ValueError("Resource unavailable")
        if transport not in {"rest", "csv"}:
            raise ValueError("Invalid extraction transport")
        definition = {"base_url": self.base_url, "endpoints": dict(self.endpoints),
            "status_map": {key: dict(value) for key, value in self.status_map.items()},
            "department_map": dict(self.department_map), "department_ids_are_canonical": self.department_ids_are_canonical,
            "max_rows": self.max_rows, "max_bytes": self.max_bytes, "max_pages_per_run": self.max_pages_per_run}
        return hashlib.sha256(json.dumps([self.connector_id, self.clinic_id, resource_kind,
            self.revision, self.cohort_hash, self.mapping_id, self.mapping_version,
            transport, definition], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
