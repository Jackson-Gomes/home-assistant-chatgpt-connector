from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import httpx
import websockets


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
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    @property
    def websocket_url(self) -> str:
        if self.base_url == "http://supervisor/core":
            return "ws://supervisor/core/websocket"

        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        path = parsed.path.rstrip("/")
        if path.endswith("/api"):
            path = path[:-4]
        path = f"{path}/api/websocket"
        return urlunparse((scheme, parsed.netloc, path, "", "", ""))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        expect_json: bool = True,
    ) -> Any:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=20.0,
            ) as client:
                response = await client.request(
                    method,
                    path,
                    json=json_data,
                    params=params,
                )
                response.raise_for_status()
                if not response.content:
                    return None
                if not expect_json:
                    return response.text
                return response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            suffix = f": {detail}" if detail else "."
            raise HomeAssistantError(
                f"Home Assistant returned HTTP {exc.response.status_code}{suffix}"
            ) from exc
        except httpx.HTTPError as exc:
            raise HomeAssistantError(f"Could not reach Home Assistant: {exc}") from exc

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        expect_json: bool = True,
    ) -> Any:
        return await self._request("GET", path, params=params, expect_json=expect_json)

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        return await self._request("POST", path, json_data=payload)

    async def check_api(self) -> dict[str, Any]:
        return await self._get("/api/")

    async def list_states(self) -> list[dict[str, Any]]:
        data = await self._get("/api/states")
        if not isinstance(data, list):
            raise HomeAssistantError("Unexpected response from /api/states.")
        return data

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        data = await self._get(f"/api/states/{quote(entity_id, safe='.')}")
        if not isinstance(data, dict):
            raise HomeAssistantError("Unexpected entity response.")
        return data

    async def list_services(self) -> list[dict[str, Any]]:
        data = await self._get("/api/services")
        if not isinstance(data, list):
            raise HomeAssistantError("Unexpected response from /api/services.")
        return data

    async def get_history(
        self,
        entity_id: str,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        minimal_response: bool = True,
    ) -> Any:
        path = "/api/history/period"
        if start_time:
            path += f"/{quote(start_time, safe='')}"
        params: dict[str, Any] = {"filter_entity_id": entity_id}
        if end_time:
            params["end_time"] = end_time
        if minimal_response:
            params["minimal_response"] = ""
        return await self._get(path, params=params)

    async def get_logbook(
        self,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        entity_id: str | None = None,
    ) -> Any:
        path = "/api/logbook"
        if start_time:
            path += f"/{quote(start_time, safe='')}"
        params: dict[str, Any] = {}
        if end_time:
            params["end_time"] = end_time
        if entity_id:
            params["entity"] = entity_id
        return await self._get(path, params=params)

    async def get_error_log(self) -> str:
        data = await self._get("/api/error_log", expect_json=False)
        return str(data or "")

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

    async def get_automation_config(self, automation_id: str) -> dict[str, Any]:
        data = await self._get(
            f"/api/config/automation/config/{quote(automation_id, safe='-_')}"
        )
        if not isinstance(data, dict):
            raise HomeAssistantError("Unexpected automation config response.")
        return data

    async def save_automation_config(
        self,
        automation_id: str,
        config: dict[str, Any],
    ) -> Any:
        return await self._post(
            f"/api/config/automation/config/{quote(automation_id, safe='-_')}",
            config,
        )

    async def get_script_config(self, script_id: str) -> dict[str, Any]:
        data = await self._get(f"/api/config/script/config/{quote(script_id, safe='-_')}")
        if not isinstance(data, dict):
            raise HomeAssistantError("Unexpected script config response.")
        return data

    async def save_script_config(
        self,
        script_id: str,
        config: dict[str, Any],
    ) -> Any:
        return await self._post(
            f"/api/config/script/config/{quote(script_id, safe='-_')}",
            config,
        )

    async def websocket_command(
        self,
        command_type: str,
        data: dict[str, Any] | None = None,
    ) -> Any:
        message = {"id": 1, "type": command_type, **(data or {})}
        try:
            async with websockets.connect(
                self.websocket_url,
                open_timeout=10,
                close_timeout=5,
                max_size=8 * 1024 * 1024,
            ) as ws:
                hello = json.loads(await ws.recv())
                if hello.get("type") != "auth_required":
                    raise HomeAssistantError(
                        f"Unexpected WebSocket handshake: {hello.get('type')!r}."
                    )

                await ws.send(
                    json.dumps({"type": "auth", "access_token": self.token})
                )
                auth = json.loads(await ws.recv())
                if auth.get("type") != "auth_ok":
                    raise HomeAssistantError(
                        f"Home Assistant WebSocket authentication failed: {auth}."
                    )

                await ws.send(json.dumps(message))
                while True:
                    response = json.loads(await ws.recv())
                    if response.get("id") != 1:
                        continue
                    if response.get("type") != "result":
                        continue
                    if not response.get("success"):
                        error = response.get("error") or {}
                        raise HomeAssistantError(
                            f"WebSocket command failed: "
                            f"{error.get('code', 'unknown')}: "
                            f"{error.get('message', 'unknown error')}"
                        )
                    return response.get("result")
        except HomeAssistantError:
            raise
        except Exception as exc:
            raise HomeAssistantError(
                f"Home Assistant WebSocket request failed: {exc}"
            ) from exc

    async def update_entity_registry(
        self,
        entity_id: str,
        changes: dict[str, Any],
    ) -> Any:
        allowed = {"name", "new_entity_id", "icon", "area_id", "aliases", "labels"}
        unknown = set(changes) - allowed
        if unknown:
            raise HomeAssistantError(
                f"Unsupported entity registry fields: {', '.join(sorted(unknown))}"
            )
        if not changes:
            raise HomeAssistantError("At least one entity registry change is required.")
        return await self.websocket_command(
            "config/entity_registry/update",
            {"entity_id": entity_id, **changes},
        )

    async def get_dashboard(self, url_path: str | None = None) -> Any:
        data: dict[str, Any] = {"force": True}
        if url_path is not None:
            data["url_path"] = url_path
        return await self.websocket_command("lovelace/config", data)

    async def save_dashboard(
        self,
        config: dict[str, Any],
        *,
        url_path: str | None = None,
    ) -> Any:
        data: dict[str, Any] = {"config": config}
        if url_path is not None:
            data["url_path"] = url_path
        return await self.websocket_command("lovelace/config/save", data)
