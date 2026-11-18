"""Bounded REST paging; continuation tokens are opaque and never destinations."""
from dataclasses import dataclass
import json
from urllib.parse import urlsplit


@dataclass(frozen=True)
class SourcePage:
    records: tuple[dict, ...]
    next_token: str | None
    binding: str
    serialized_bytes: int
    transport: str = "rest"


class RestSource:
    def __init__(self, profile, client, headers=None):
        self.profile = profile
        self.client = client
        self.headers = {**dict(headers or {}), "Accept-Encoding": "identity"}

    async def iter_pages(self, resource_kind, *, checkpoint=None):
        binding = self.profile.cursor_binding(resource_kind)
        token = None
        if checkpoint:
            if checkpoint.get("binding") != binding:
                raise ValueError("Extraction scope changed; controlled resync required")
            token = checkpoint.get("token")
            if token is not None and not isinstance(token, str):
                raise ValueError("Invalid opaque token")
        seen = set()
        base = urlsplit(self.profile.base_url)
        url = f"{base.scheme}://{base.netloc}{self.profile.endpoints[resource_kind]}"
        for _ in range(self.profile.max_pages_per_run):
            if token in seen:
                raise ValueError("Repeated continuation token")
            seen.add(token)
            params = {"limit": self.profile.max_rows}
            if token is not None:
                params["cursor"] = token
            try:
                async with self.client.stream("GET", url, params=params, headers=self.headers,
                                              follow_redirects=False, timeout=30) as response:
                    if response.status_code != 200:
                        raise ValueError("Source read failed")
                    if response.headers.get("content-encoding", "identity") != "identity":
                        raise ValueError("Compressed source pages are not enabled in this profile")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > self.profile.max_bytes:
                            raise ValueError("Source page exceeds byte limit")
                        body.extend(chunk)
                data = json.loads(body, parse_float=str)
            except ValueError:
                raise
            except Exception:
                raise RuntimeError("Source transport unavailable") from None
            records = data.get("records") if isinstance(data, dict) else None
            following = data.get("next") if isinstance(data, dict) else None
            if not isinstance(records, list) or len(records) > self.profile.max_rows or not all(isinstance(row, dict) for row in records):
                raise ValueError("Invalid or oversized source page")
            if following is not None and (not isinstance(following, str) or not following or len(following) > 4096):
                raise ValueError("Invalid opaque continuation token")
            if following in seen and following is not None:
                raise ValueError("Repeated continuation token")
            yield SourcePage(tuple(records), following, binding, len(body))
            if following is None:
                break
            token = following
