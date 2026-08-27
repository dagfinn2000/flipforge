from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

from .config import settings

log = logging.getLogger("flipforge.wiki")

# The v2 timeseries endpoint takes a `lookback` rather than a step, and picks the
# resolution itself. Verified against the live API: 6h and 24h return 5 minute
# points, 7d hourly, 30d six-hourly, 6m and 1y daily.
LOOKBACKS = ("6h", "24h", "7d", "30d", "6m", "1y")
TIMESTEPS = ("5m", "1h", "6h", "24h")
STEP_SECONDS = {"5m": 300, "1h": 3600, "6h": 21600, "24h": 86400}

# Which lookback to request when the UI asks for a given candle resolution.
LOOKBACK_FOR_TIMESTEP = {"5m": "24h", "1h": "7d", "6h": "30d", "24h": "1y"}
TIMESTEP_FOR_LOOKBACK = {"6h": "5m", "24h": "5m", "7d": "1h", "30d": "6h", "6m": "24h", "1y": "24h"}


class WikiClient:
    """Async client for the OSRS Wiki real-time prices API (v2).

    Free and unauthenticated. The only rule is a descriptive User-Agent -- the
    wiki pre-emptively rejects default agents, so a 400 here almost always means
    the header did not get set rather than that the request was malformed.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.wiki_base,
            headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )
        # Upstream has no hard rate limit precisely because clients behave.
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
                if resp.status_code in (400, 403):
                    # Not retryable: this is the wiki rejecting the client, not
                    # a transient fault. Surface it with the actual reason.
                    raise RuntimeError(
                        f"upstream rejected the request ({resp.status_code}) for {path}: "
                        f"{resp.text[:200]}. Check that FF_CONTACT is set and that the "
                        f"User-Agent reads like '{settings.user_agent}'."
                    )
                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", delay))
                    log.warning("rate limited on %s, sleeping %.1fs", path, wait)
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except RuntimeError:
                raise
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
        """Every item's averages for one interval. Only 5m and 1h exist in bulk."""
        if timestep not in ("5m", "1h"):
            raise ValueError("bulk endpoints only exist for 5m and 1h")
        params = {"timestamp": timestamp} if timestamp is not None else None
        return (await self._get(f"/{timestep}", params)).get("data", {})

    async def timeseries(self, item_id: int, lookback: str) -> list[dict[str, Any]]:
        """History for a single item. Used only for lazy deep-history on demand."""
        if lookback not in LOOKBACKS:
            raise ValueError(f"lookback must be one of {LOOKBACKS}")
        data = await self._get("/timeseries", {"id": item_id, "lookback": lookback})
        return data.get("data", [])

    async def probe(self) -> None:
        """One cheap call at boot so a rejected User-Agent fails fast and loudly."""
        await self._get("/latest", {"id": 2})
        log.info("upstream reachable as %s", settings.user_agent)


client = WikiClient()
