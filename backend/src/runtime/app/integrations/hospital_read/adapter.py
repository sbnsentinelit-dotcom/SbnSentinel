"""H04 read adapter. No direct report writes or rule/external-action calls."""
import inspect
import hashlib
import json
from app.integrations.core.contracts import IntegrationAdapter, ConnectorFactory
from app.integrations.hospital_read.mappings import normalize_record
from app.integrations.hospital_read.rest_source import RestSource
from app.integrations.hospital_read.csv_source import CsvSource
from app.integrations.hospital_read.manifest import HospitalReadProfile


class HospitalReadAdapter(IntegrationAdapter):
    def __init__(self, profile, client, *, authorize, intake=None, headers=None):
        self.profile = profile
        self.authorize = authorize
        self.intake = intake
        self.rest = RestSource(profile, client, headers)

    def check(self, resource_kind):
        if resource_kind not in self.profile.endpoints:
            raise PermissionError("Resource capability unavailable")
        # The server callback must verify current binding, actual readiness and grants.
        self.authorize(self.profile, resource_kind)

    async def get_capability_statement(self):
        resources = []
        for kind in self.profile.endpoints:
            self.check(kind)
            resources.append({"type": kind, "interaction": [{"code": "read"}]})
        return {"resourceType": "CapabilityStatement", "status": "active", "kind": "instance",
                "protocol": "REST/JSON", "fhir_supported": False,
                "profile_revision": self.profile.revision, "rest": [{"mode": "server", "resource": resources}]}

    async def iter_pages(self, resource_kind, *, checkpoint=None):
        self.check(resource_kind)
        async for page in self.rest.iter_pages(resource_kind, checkpoint=checkpoint):
            self.check(resource_kind)
            yield page

    async def get_resource(self, resource_type, query_params=None):
        params = query_params or {}
        checkpoint = params.get("checkpoint")
        async for page in self.iter_pages(resource_type, checkpoint=checkpoint):
            if page.next_token is not None:
                raise ValueError("Multi-page resource requires iter_pages; truncation refused")
            return list(page.records)
        return []

    async def _submit(self, resource_kind, page):
        self.check(resource_kind)
        if self.intake is None or getattr(self.intake, "hospital_contract_revision", None) != "hsp01-v1":
            raise RuntimeError("Hospital shared intake prerequisites are not ready")
        records, rejected, positions = [], [], []
        for index, raw in enumerate(page.records):
            try:
                records.extend(normalize_record(raw, resource_kind=resource_kind, context=self.profile))
                positions.append(index)
            except (ValueError, TypeError):
                # No raw data in diagnostics. Intake persists the quarantine receipt.
                rejected.append({"row_index": index, "reason": "INVALID_SOURCE_ROW", "payload": raw})
        page_id = hashlib.sha256(json.dumps([page.binding, page.next_token, page.records],
                                            sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        result = self.intake.submit_hospital_page(self.profile, resource_kind, records, rejected,
                                                  {"binding": page.binding, "token": page.next_token, "transport": page.transport,
                                                   "page_id": page_id, "record_positions": positions}, raw_rows=list(page.records))
        if inspect.isawaitable(result):
            result = await result
        required = {"accepted", "duplicate", "quarantined", "deferred", "failed"}
        if not required.issubset(result) or any(type(result[k]) is not int or result[k] < 0 for k in required):
            raise RuntimeError("Invalid durable intake receipt")
        if sum(result[k] for k in required) != len(page.records):
            raise RuntimeError("Unaccounted source rows")
        return result

    async def sync(self):
        counts = dict.fromkeys(("accepted", "duplicate", "quarantined", "deferred", "failed"), 0)
        partial = False
        try:
            for kind in self.profile.endpoints:
                checkpoint = self.intake.get_hospital_checkpoint(self.profile, kind) if self.intake else None
                remaining = False
                async for page in self.iter_pages(kind, checkpoint=checkpoint):
                    result = await self._submit(kind, page)
                    for key in counts:
                        counts[key] += result[key]
                    if result["failed"] or result["deferred"]:
                        return {"status": "PARTIAL", **counts}
                    remaining = page.next_token is not None
                partial = partial or remaining
        except PermissionError:
            raise
        except Exception:
            # Counts describe acknowledged durable rows only. The failed range
            # is explicitly unknown, never reported as successful empty data.
            return {"status": "PARTIAL", **counts, "unaccounted_range": True,
                    "source_error": "SOURCE_OR_INTAKE_UNAVAILABLE"}
        return {"status": "PARTIAL" if partial else "COMPLETE", **counts}

    async def import_csv(self, lines, resource_kind):
        counts = dict.fromkeys(("accepted", "duplicate", "quarantined", "deferred", "failed"), 0)
        try:
            for page in CsvSource(self.profile).iter_pages(lines, resource_kind):
                result = await self._submit(resource_kind, page)
                for key in counts:
                    counts[key] += result[key]
                if result["failed"] or result["deferred"]:
                    return {"status": "PARTIAL", **counts}
        except PermissionError:
            raise
        except Exception:
            return {"status": "PARTIAL", **counts, "unaccounted_range": True,
                    "source_error": "CSV_OR_INTAKE_UNAVAILABLE"}
        return {"status": "COMPLETE", **counts}


class HospitalReadFactory(ConnectorFactory):
    @classmethod
    def from_validated_config(cls, config):
        # Dependencies are server-owned injected objects, never browser credentials.
        if (not isinstance(config.get("profile"), dict) or "client" not in config
                or not callable(config.get("authorize"))):
            raise ValueError("Hospital source requires a reviewed profile and server-resolved transport/authority")
        return HospitalReadAdapter(HospitalReadProfile(**config["profile"]), config["client"],
                                   authorize=config["authorize"], intake=config.get("intake"), headers=config.get("headers"))
