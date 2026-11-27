"""CSV exports from clinic staff: one template per evidence source, parsed into intake records.

One row is one record; `kind` says which. A payment advice with several components uses one row per
component with the same key. Amounts are written in the currency (900.00, -5.00) and stored in minor
units. Times may carry a zone (2026-11-24T10:00:00Z, +04:00); a time without a zone is clinic local
time. Every problem is reported with its row number and nothing is stored from a file with problems.
"""
import csv
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Tuple

from .intake import KIND_ROLE

LINE = ["book", "claim", "line"]
PART = ["part", "part_type", "part_amount", "part_book", "part_claim", "part_line", "adjustment_ref",
        "original_advice", "original_part"]
BASE = ["key", "kind", "effective", "seen", "evidence"]
FIELDS: Dict[str, List[str]] = {
    "control": LINE + ["bucket", "amount"], "opening": LINE + ["bucket", "amount"],
    "closing": LINE + ["bucket", "amount"], "new_line": LINE,
    "archive_doc": LINE + ["artifact", "content_hash"],
    "assignment": LINE + ["owner", "accepted_ref", "next_action"],
    "adjustment_owner": ["adjustment_ref", "owner", "accepted_ref"],
    "advice": ["trace", "net", "settlement"] + PART, "deposit": ["trace", "amount"],
    "posting": ["advice"] + PART, "movement": LINE + ["bucket", "amount", "reason", "link"],
    "approval": LINE + ["bucket", "amount", "reason"],
    "denial": ["advice"] + LINE + ["notice", "route", "rule", "due"],
    "appeal": ["denial", "stage", "receipt"],
    "probe": ["stage"] + LINE + ["originated_at", "route", "source_doc", "receipt"],
}
# Kinds a source role may deliver (probe: operations or archive; appeal: operations sends, payer answers).
ROLE_KINDS: Dict[str, List[str]] = {}
for _kind, _role in KIND_ROLE.items():
    ROLE_KINDS.setdefault(_role, []).append(_kind)
ROLE_KINDS["operations"] += ["probe"]
ROLE_KINDS["archive"] += ["probe"]
ROLE_KINDS["payer"] += ["appeal"]

EXAMPLES: Dict[str, List[dict]] = {
    "control": [dict(key="control-c7", kind="control", effective="2026-11-01 00:00", book="legacy", claim="C7",
                     line="01", bucket="payer_ar", amount="1000.00"),
                dict(key="closing-c7", kind="closing", effective="2026-11-05 22:00", book="legacy", claim="C7",
                     line="01", bucket="payer_ar", amount="300.00")],
    "legacy": [dict(key="opening-c7", kind="opening", effective="2026-11-01 00:00", book="legacy", claim="C7",
                    line="01", bucket="payer_ar", amount="1000.00")],
    "destination": [dict(key="new-c7", kind="new_line", effective="2026-11-01 00:00", book="new", claim="C7", line="01")],
    "payer": [dict(key="advice-1", kind="advice", effective="2026-11-04 14:00", trace="pay-9", net="900.00",
                   settlement="cash", part="A", part_type="claim_payment", part_amount="700.00", part_book="legacy",
                   part_claim="C7", part_line="01"),
              dict(key="advice-1", kind="advice", effective="2026-11-04 14:00", trace="pay-9", net="900.00",
                   settlement="cash", part="B", part_type="claim_payment", part_amount="200.00", part_book="new",
                   part_claim="C7", part_line="01"),
              dict(key="den-1", kind="denial", effective="2026-10-28 10:00", book="legacy", claim="C7", line="01",
                   notice="notice-1", route="payer-portal", rule="APPEAL-30-DAYS", due="2026-11-27 18:00")],
    "bank": [dict(key="bank-1", kind="deposit", effective="2026-11-04 14:00", trace="pay-9", amount="900.00")],
    "ledger": [dict(key="ledger-A", kind="posting", effective="2026-11-04 14:00", advice="advice-1", part="A",
                    part_type="claim_payment", part_amount="700.00", part_book="legacy", part_claim="C7", part_line="01"),
               dict(key="delta-A", kind="movement", effective="2026-11-04 14:00", book="legacy", claim="C7",
                    line="01", bucket="payer_ar", amount="-700.00", reason="payer_cash", link="ledger-A")],
    "operations": [dict(key="owner-c7", kind="assignment", effective="2026-11-01 00:00", book="legacy", claim="C7",
                        line="01", owner="bill-team", accepted_ref="accept-1", next_action="monitor"),
                   dict(key="appeal-1-sent", kind="appeal", effective="2026-11-20 09:00", denial="den-1",
                        stage="sent", receipt="appeal-ref-1")],
    "archive": [dict(key="original-c7", kind="archive_doc", effective="2026-10-15 09:00", book="legacy", claim="C7",
                     line="01", artifact="original_submission", content_hash="a" * 64)],
}


def columns(role: str) -> List[str]:
    out = list(BASE)
    for kind in ROLE_KINDS.get(role, []):
        for c in FIELDS[kind]:
            if c not in out:
                out.append(c)
    return out


def template(role: str) -> str:
    """The CSV header of a source with example rows (replace them with the export)."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns(role), lineterminator="\n")
    w.writeheader()
    for row in EXAMPLES.get(role, []):
        w.writerow(row)
    return buf.getvalue()


class RowError(Exception):
    pass


def _minor(value: str, name: str) -> int:
    try:
        d = Decimal(value.strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        raise RowError(f"{name} must be an amount such as 900.00")
    minor = d * 100
    if minor != minor.to_integral_value():
        raise RowError(f"{name} has more than two decimals")
    return int(minor)


def _instant(value: str, name: str, offset: timedelta) -> str:
    text = (value or "").strip()
    if not text:
        raise RowError(f"{name} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise RowError(f"{name} must be a date and time such as 2026-11-24 10:00")
    if parsed.tzinfo is None:                       # clinic local time
        parsed = (parsed - offset).replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _line(row: dict, prefix: str = "") -> dict:
    vals = {k: (row.get(prefix + k) or "").strip() for k in LINE}
    missing = [prefix + k for k, v in vals.items() if not v]
    if missing:
        raise RowError(f"{', '.join(missing)} required")
    return vals


def _part(row: dict, offset: timedelta) -> dict:
    part = {"part": (row.get("part") or "").strip(), "type": (row.get("part_type") or "").strip(),
            "minor": _minor(row.get("part_amount") or "", "part_amount")}
    if any((row.get("part_" + k) or "").strip() for k in LINE):
        part["line"] = _line(row, "part_")
    for k in ("adjustment_ref", "original_advice", "original_part"):
        if (row.get(k) or "").strip():
            part[k] = row[k].strip()
    return part


def _text(row: dict, *names: str) -> dict:
    out = {}
    for n in names:
        v = (row.get(n) or "").strip()
        if not v:
            raise RowError(f"{n} is required")
        out[n] = v
    return out


def _data(kind: str, row: dict, offset: timedelta) -> dict:
    if kind in ("control", "opening", "closing"):
        return {"line": _line(row), **_text(row, "bucket"), "minor": _minor(row.get("amount") or "", "amount")}
    if kind == "new_line":
        return {"line": _line(row)}
    if kind == "archive_doc":
        return {"line": _line(row), **_text(row, "artifact", "content_hash")}
    if kind == "assignment":
        return {"line": _line(row), **_text(row, "owner", "accepted_ref", "next_action")}
    if kind == "adjustment_owner":
        return _text(row, "adjustment_ref", "owner", "accepted_ref")
    if kind == "advice":
        return {**_text(row, "trace", "settlement"), "net": _minor(row.get("net") or "", "net"),
                "parts": [_part(row, offset)]}
    if kind == "deposit":
        return {**_text(row, "trace"), "minor": _minor(row.get("amount") or "", "amount")}
    if kind == "posting":
        return {**_text(row, "advice"), "component": _part(row, offset)}
    if kind == "movement":
        return {"line": _line(row), **_text(row, "bucket", "reason", "link"),
                "minor": _minor(row.get("amount") or "", "amount")}
    if kind == "approval":
        return {"line": _line(row), **_text(row, "bucket", "reason"), "minor": _minor(row.get("amount") or "", "amount")}
    if kind == "denial":
        out = {"line": _line(row), **_text(row, "notice", "route")}
        out["advice"] = (row.get("advice") or "").strip() or None
        out["rule"] = (row.get("rule") or "").strip() or None
        out["due"] = _instant(row["due"], "due", offset) if (row.get("due") or "").strip() else None
        return out
    if kind == "appeal":
        return _text(row, "denial", "stage", "receipt")
    if kind == "probe":
        return {"line": _line(row), **_text(row, "stage", "route", "source_doc", "receipt"),
                "originated_at": _instant(row.get("originated_at") or "", "originated_at", offset)}
    raise RowError(f"unknown kind '{kind}'")


def parse(role: str, text: str, offset: timedelta) -> Tuple[List[dict], List[dict]]:
    """(records in the intake shape, problems [{row, reason}]). Rows of one advice key merge into one
    advice with all its components; they must agree on trace, net, settlement and dates."""
    problems: List[dict] = []
    records: Dict[str, dict] = {}
    order: List[str] = []
    allowed = set(ROLE_KINDS.get(role, []))
    try:
        reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
        header = reader.fieldnames or []
    except csv.Error as e:
        return [], [{"row": 1, "reason": f"not a readable CSV file: {e}"}]
    if "key" not in header or "kind" not in header:
        return [], [{"row": 1, "reason": "the header must include key and kind (download the template)"}]
    for n, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue
        try:
            key, kind = (row.get("key") or "").strip(), (row.get("kind") or "").strip()
            if not key:
                raise RowError("key is required")
            if kind not in allowed:
                raise RowError(f"a {role} source cannot deliver '{kind}' records" if kind else "kind is required")
            effective = _instant(row.get("effective") or "", "effective", offset)
            seen = _instant(row.get("seen") or row.get("effective") or "", "seen", offset)
            data = {"kind": kind, **_data(kind, row, offset)}
            rec = {"key": key, "evidence": (row.get("evidence") or "").strip() or f"upload-{key}",
                   "effective": effective, "seen": seen, "revision": 1, "data": data}
            ident = f"{kind}:{key}"
            if kind == "advice" and ident in records:
                first = records[ident]
                same = {k: v for k, v in first["data"].items() if k != "parts"}
                if (first["effective"], first["seen"], same) != (effective, seen, {k: v for k, v in data.items() if k != "parts"}):
                    raise RowError(f"advice {key} rows disagree on trace, net, settlement or dates")
                first["data"]["parts"].append(data["parts"][0])
                continue
            if ident in records:
                raise RowError(f"{kind} {key} appears twice")
            records[ident] = rec
            order.append(ident)
        except RowError as e:
            problems.append({"row": n, "reason": str(e)})
    return [records[i] for i in order], problems
