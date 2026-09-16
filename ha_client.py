from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()


class HomeAssistantError(RuntimeError):
    pass


class HomeAssistantClient:
    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("HA_URL", "")).rstrip("/")
        self.token = token or os.getenv("HA_TOKEN", "")
        if not self.base_url:
            raise HomeAssistantError("HA_URL is not configured.")
        if not self.token:
            raise HomeAssistantError("HA_TOKEN is not configured.")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> Any:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=10.0,
            ) as client:
                response = await client.request(method, path, json=json)
                response.raise_for_status()
                if not response.content:
                    return None
                return response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            suffix = f": {detail}" if detail else "."
            raise HomeAssistantError(
                f"Home Assistant returned HTTP {exc.response.status_code}{suffix}"
            ) from exc
        except httpx.HTTPError as exc:
            raise HomeAssistantError(f"Could not reach Home Assistant: {exc}") from exc

    async def _get(self, path: str) -> Any:
        return await self._request("GET", path)

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        return await self._request("POST", path, json=payload)

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

    async def call_service(
        self,
        domain: str,
        service: str,
        *,
        entity_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        payload = dict(data or {})
        if entity_id:
            payload["entity_id"] = entity_id
        return await self._post(f"/api/services/{domain}/{service}", payload)
