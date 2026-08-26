from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

from .config import settings

log = logging.getLogger("flipforge.wiki")

VALID_TIMESTEPS = ("5m", "1h", "6h", "24h")
STEP_SECONDS = {"5m": 300, "1h": 3600, "6h": 21600, "24h": 86400}


class WikiClient:
    """Thin async client for the OSRS Wiki real-time prices API.

    The API is free and unauthenticated; the only rule is to send a descriptive
    User-Agent so the maintainers can contact whoever is hammering them.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.wiki_base,
            headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )
        # Upstream is generous but shared; keep concurrency modest and polite.
        self._sem = asyncio.Semaphore(4)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None, tries: int = 4) -> Any:
        delay = 1.0
        last: Exception | None = None
        for attempt in range(1, tries + 1):
            try:
                async with self._sem:
                    resp = await self._client.get(path, params=params)
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", delay))
                    log.warning("rate limited on %s, sleeping %.1fs", path, retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001 - network flakiness is expected
                last = exc
                if attempt == tries:
                    break
                await asyncio.sleep(delay)
                delay *= 2
        raise RuntimeError(f"GET {path} failed after {tries} tries: {last}")

    async def mapping(self) -> list[dict[str, Any]]:
        return await self._get("/mapping")

    async def latest(self) -> dict[str, dict[str, Any]]:
        return (await self._get("/latest")).get("data", {})

    async def bulk(self, timestep: str, timestamp: Optional[int] = None) -> dict[str, dict[str, Any]]:
        """Every item's averages for a single interval. timestep is 5m or 1h."""
        if timestep not in ("5m", "1h"):
            raise ValueError("bulk endpoints only exist for 5m and 1h")
        params = {"timestamp": timestamp} if timestamp is not None else None
        return (await self._get(f"/{timestep}", params)).get("data", {})

    async def timeseries(self, item_id: int, timestep: str) -> list[dict[str, Any]]:
        """Up to 365 points of history for a single item at the given timestep."""
        if timestep not in VALID_TIMESTEPS:
            raise ValueError(f"timestep must be one of {VALID_TIMESTEPS}")
        data = await self._get("/timeseries", {"id": item_id, "timestep": timestep})
        return data.get("data", [])


client = WikiClient()
