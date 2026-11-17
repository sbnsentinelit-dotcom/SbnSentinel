"""H03: unambiguous, versioned source keys; department is never identity."""
import hashlib
import json

SCHEME = "hospital-source-v1"
PARENT_LOCAL = frozenset({"ClaimLine", "SubmissionAttempt"})


def required_id(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required {label}")
    return value


def entity_key(*, org_id, clinic_id, connector_id, resource_kind, external_id, parent_id=None):
    fields = [required_id(value, name) for name, value in (
        ("org_id", org_id), ("clinic_id", clinic_id), ("connector_id", connector_id),
        ("resource_kind", resource_kind), ("external_id", external_id))]
    if resource_kind in PARENT_LOCAL:
        parent_id = required_id(parent_id, "parent claim")
    elif parent_id is not None:
        parent_id = required_id(parent_id, "parent_id")
    encoded = json.dumps([SCHEME, *fields[:4], parent_id, fields[4]],
                         ensure_ascii=False, separators=(",", ":"))
    return SCHEME + ":" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def delivery_identity(entity, source_version):
    value = json.dumps([entity, required_id(source_version, "source_version")], separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()
