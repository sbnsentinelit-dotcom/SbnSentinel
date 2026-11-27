"""Stable JSON for logical evidence identity, including timezone equivalence."""
import hashlib
import json
from datetime import datetime, timezone


def canonical(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [canonical(v) for v in value]
    return value


def encode(value):
    return json.dumps(canonical(value), sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()
