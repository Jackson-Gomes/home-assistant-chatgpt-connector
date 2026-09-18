from __future__ import annotations

from typing import Any, Callable


ASSISTANT_ID = "conversation"
_MAX_ENTITY_BATCH = 512
_PIPELINE_FIELDS = {
    "conversation_engine",
    "conversation_language",
    "language",
    "name",
    "stt_engine",
    "stt_language",
    "tts_engine",
    "tts_language",
    "tts_voice",
    "wake_word_entity",
    "wake_word_id",
    "prefer_local_intents",
}
_NULLABLE_PIPELINE_FIELDS = {
    "stt_engine",
    "stt_language",
    "tts_engine",
    "tts_language",
    "tts_voice",
    "wake_word_entity",
    "wake_word_id",
}


def _clean_entity_id(value: str, *, required_domain: str | None = None) -> str:
    if not isinstance(value, str):
        raise ValueError("entity_id must be a string.")
    cleaned = value.strip().lower()
    if not cleaned or "." not in cleaned or len(cleaned) > 255:
        raise ValueError("entity_id must be in domain.object_id format.")
    domain, object_id = cleaned.split(".", 1)
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_")
    if not domain or not object_id or any(char not in allowed for char in domain + object_id):
        raise ValueError(f"Invalid entity_id: {value!r}.")
    if required_domain is not None and domain != required_domain:
        raise ValueError(f"entity_id must use the {required_domain}. domain.")
    return cleaned


def _clean_pipeline_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("pipeline_id must be a string.")
    cleaned = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if not cleaned or len(cleaned) > 128 or any(char not in allowed for char in cleaned):
        raise ValueError("pipeline_id contains unsupported characters.")
    return cleaned


def _clean_nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string.")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field} cannot be empty.")
    if len(cleaned) > 255:
        raise ValueError(f"{field} is too long.")
    return cleaned


def validate_pipeline_changes(changes: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(changes, dict) or not changes:
        raise ValueError("changes must be a non-empty object.")
    unknown = set(changes) - _PIPELINE_FIELDS
    if unknown:
        raise ValueError(
            "Unsupported Assist pipeline fields: " + ", ".join(sorted(unknown))
        )

    validated: dict[str, Any] = {}
    for field, value in changes.items():
        if field == "prefer_local_intents":
            if not isinstance(value, bool):
                raise ValueError("prefer_local_intents must be boolean.")
            validated[field] = value
            continue

        if value is None:
            if field not in _NULLABLE_PIPELINE_FIELDS:
                raise ValueError(f"{field} cannot be null.")
            validated[field] = None
            continue

        if field == "conversation_engine":
            validated[field] = _clean_entity_id(value, required_domain="conversation")
        elif field == "wake_word_entity":
            validated[field] = _clean_entity_id(value)
        else:
            validated[field] = _clean_nonempty_string(value, field)

    return validated


async def list_pipelines(ha: Any) -> dict[str, Any]:
    result = await ha.websocket_command("assist_pipeline/pipeline/list")
    if not isinstance(result, dict):
        raise ValueError("Unexpected Assist pipeline list response.")
    pipelines = result.get("pipelines", [])
    if not isinstance(pipelines, list):
        raise ValueError("Unexpected Assist pipeline list response.")
    return {
        "count": len(pipelines),
        "pipelines": pipelines,
        "preferred_pipeline": result.get("preferred_pipeline"),
    }


async def update_pipeline(
    ha: Any,
    pipeline_id: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    clean_pipeline_id = _clean_pipeline_id(pipeline_id)
    validated_changes = validate_pipeline_changes(changes)

    current = await ha.websocket_command(
        "assist_pipeline/pipeline/get",
        {"pipeline_id": clean_pipeline_id},
    )
    if not isinstance(current, dict):
        raise ValueError("Unexpected Assist pipeline response.")

    payload = {field: current[field] for field in _PIPELINE_FIELDS if field in current}
    payload.update(validated_changes)
    payload["pipeline_id"] = clean_pipeline_id

    result = await ha.websocket_command("assist_pipeline/pipeline/update", payload)
    if not isinstance(result, dict):
        raise ValueError("Unexpected Assist pipeline update response.")
    return {
        "pipeline_id": clean_pipeline_id,
        "changes": validated_changes,
        "pipeline": result,
    }


async def list_exposed_entities(ha: Any) -> dict[str, Any]:
    result = await ha.websocket_command("homeassistant/expose_entity/list")
    if not isinstance(result, dict):
        raise ValueError("Unexpected exposed-entities response.")
    raw = result.get("exposed_entities", {})
    if not isinstance(raw, dict):
        raise ValueError("Unexpected exposed-entities response.")

    exposed = sorted(
        entity_id
        for entity_id, settings in raw.items()
        if isinstance(settings, dict) and settings.get(ASSISTANT_ID) is True
    )
    return {
        "assistant": ASSISTANT_ID,
        "count": len(exposed),
        "entity_ids": exposed,
    }


async def set_entity_exposure(ha: Any, entity_id: str, exposed: bool) -> dict[str, Any]:
    clean_entity_id = _clean_entity_id(entity_id)
    if not isinstance(exposed, bool):
        raise ValueError("exposed must be boolean.")

    # Validate the entity against the Home Assistant state API before changing exposure.
    await ha.get_state(clean_entity_id)
    await ha.websocket_command(
        "homeassistant/expose_entity",
        {
            "assistants": [ASSISTANT_ID],
            "entity_ids": [clean_entity_id],
            "should_expose": exposed,
        },
    )
    return {
        "assistant": ASSISTANT_ID,
        "entity_id": clean_entity_id,
        "exposed": exposed,
    }


async def set_exact_exposed_entities(ha: Any, entity_ids: list[str]) -> dict[str, Any]:
    if not isinstance(entity_ids, list):
        raise ValueError("entity_ids must be a list.")
    if len(entity_ids) > _MAX_ENTITY_BATCH:
        raise ValueError(f"entity_ids cannot contain more than {_MAX_ENTITY_BATCH} items.")

    target: list[str] = []
    seen: set[str] = set()
    for value in entity_ids:
        clean = _clean_entity_id(value)
        if clean not in seen:
            target.append(clean)
            seen.add(clean)

    # Validate all targets before making any change.
    for entity_id in target:
        await ha.get_state(entity_id)

    current = await list_exposed_entities(ha)
    current_set = set(current["entity_ids"])
    target_set = set(target)
    to_enable = sorted(target_set - current_set)
    to_disable = sorted(current_set - target_set)

    # Disable automatic exposure so the requested list becomes an allowlist.
    await ha.websocket_command(
        "homeassistant/expose_new_entities/set",
        {"assistant": ASSISTANT_ID, "expose_new": False},
    )

    # Enable desired entities before disabling extras to preserve the requested target
    # even if Home Assistant rejects a later operation.
    if to_enable:
        await ha.websocket_command(
            "homeassistant/expose_entity",
            {
                "assistants": [ASSISTANT_ID],
                "entity_ids": to_enable,
                "should_expose": True,
            },
        )
    if to_disable:
        await ha.websocket_command(
            "homeassistant/expose_entity",
            {
                "assistants": [ASSISTANT_ID],
                "entity_ids": to_disable,
                "should_expose": False,
            },
        )

    final = await list_exposed_entities(ha)
    return {
        "assistant": ASSISTANT_ID,
        "expose_new": False,
        "requested_entity_ids": target,
        "enabled": to_enable,
        "disabled": to_disable,
        "entity_ids": final["entity_ids"],
        "count": final["count"],
    }


def register_assist_tools(mcp: Any, client_factory: Callable[[], Any]) -> None:
    """Register restricted Home Assistant Assist administration MCP tools."""

    @mcp.tool()
    async def list_assist_pipelines() -> dict[str, Any]:
        """List Home Assistant Assist pipelines and their preferred pipeline."""
        try:
            return {"ok": True, **await list_pipelines(client_factory())}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    async def update_assist_pipeline(
        pipeline_id: str,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        """Update one Assist pipeline using an allowlist of official pipeline fields.

        For the Codex Assist agent, set changes={"conversation_engine":
        "conversation.codex_assist"}. This uses Home Assistant's WebSocket API and
        never edits .storage directly.
        """
        try:
            return {
                "ok": True,
                **await update_pipeline(client_factory(), pipeline_id, changes),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    async def list_assist_exposed_entities() -> dict[str, Any]:
        """List entities explicitly exposed to Home Assistant Assist (conversation)."""
        try:
            return {"ok": True, **await list_exposed_entities(client_factory())}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    async def set_assist_entity_exposure(
        entity_id: str,
        exposed: bool,
    ) -> dict[str, Any]:
        """Expose or unexpose exactly one entity to Home Assistant Assist."""
        try:
            return {
                "ok": True,
                **await set_entity_exposure(client_factory(), entity_id, exposed),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    async def set_assist_exposed_entities(entity_ids: list[str]) -> dict[str, Any]:
        """Set the exact Assist exposure allowlist and disable automatic exposure.

        Example for the initial safe scope:
        entity_ids=["light.luz_do_escritorio"]
        """
        try:
            return {
                "ok": True,
                **await set_exact_exposed_entities(client_factory(), entity_ids),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
