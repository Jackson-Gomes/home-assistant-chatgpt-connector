from __future__ import annotations
import os
from typing import Any
import httpx

class HomeAssistantError(RuntimeError):
    pass

class HomeAssistantClient:
    def __init__(self) -> None:
        self.base_url = os.environ.get("HA_URL", "http://supervisor/core").rstrip("/")
        self.token = os.environ.get("HA_TOKEN", "")
        if not self.token:
            raise HomeAssistantError("Home Assistant Supervisor token is unavailable.")

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    async def _get(self, path: str) -> Any:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, headers=self.headers, timeout=10.0) as client:
                response = await client.get(path)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise HomeAssistantError(f"Home Assistant returned HTTP {exc.response.status_code}.") from exc
        except httpx.HTTPError as exc:
            raise HomeAssistantError(f"Could not reach Home Assistant: {exc}") from exc

    async def check_api(self) -> dict[str, Any]:
        return await self._get("/api/")

    async def list_states(self) -> list[dict[str, Any]]:
        data = await self._get("/api/states")
        if not isinstance(data, list):
            raise HomeAssistantError("Unexpected response from /api/states.")
        return data

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        data = await self._get(f"/api/states/{entity_id}")
        if not isinstance(data, dict):
            raise HomeAssistantError("Unexpected entity response.")
        return data
