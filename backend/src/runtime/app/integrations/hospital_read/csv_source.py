"""Strict CSV chunks entering the same normalizer/intake as REST.

Accepts a bounded-line iterator; no full-file read or CSV row-list accumulation.
Empty cells mean unavailable/absent. Literal null explicitly means null.
"""
import csv
import json
from app.integrations.hospital_read.mappings import FIELDS
from app.integrations.hospital_read.rest_source import SourcePage


class CsvSource:
    def __init__(self, profile):
        self.profile = profile

    def iter_pages(self, lines, resource_kind):
        binding = self.profile.cursor_binding(resource_kind, "csv")
        consumed = 0
        def bounded_lines():
            nonlocal consumed
            for line in lines:
                if not isinstance(line, str):
                    raise ValueError("CSV input must contain text lines")
                consumed += len(line.encode("utf-8"))
                if consumed > self.profile.max_bytes:
                    raise ValueError("CSV logical record exceeds byte limit")
                yield line
        reader = csv.DictReader(bounded_lines(), strict=True)
        headers = reader.fieldnames
        if not headers or len(headers) != len(set(headers)) or not {"id", "source_version", "status"}.issubset(headers) or not set(headers).issubset(FIELDS):
            raise ValueError("Invalid CSV headers")
        consumed = 0
        batch, size, row_index = [], 2, 0
        for values in reader:
            consumed = 0
            if None in values or any(value is None for value in values.values()):
                raise ValueError("CSV column count mismatch")
            row = {}
            for key, value in values.items():
                if value == "":
                    continue
                if value == "null":
                    row[key] = None
                elif key in {"deleted", "bank_confirmed"}:
                    if value not in {"true", "false"}:
                        raise ValueError("Invalid CSV boolean")
                    row[key] = value == "true"
                else:
                    row[key] = value
            cost = len(json.dumps(row, separators=(",", ":"), ensure_ascii=False).encode()) + 1
            if cost + 2 > self.profile.max_bytes:
                raise ValueError("CSV record exceeds byte limit")
            if batch and (len(batch) >= self.profile.max_rows or size + cost > self.profile.max_bytes):
                yield SourcePage(tuple(batch), str(row_index), binding, size, "csv")
                batch, size = [], 2
            batch.append(row)
            size += cost
            row_index += 1
        if batch:
            yield SourcePage(tuple(batch), None, binding, size, "csv")
