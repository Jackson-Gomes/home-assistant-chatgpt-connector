import asyncio

import pytest

from assist_admin import (
    list_exposed_entities,
    list_pipelines,
    set_entity_exposure,
    set_exact_exposed_entities,
    update_pipeline,
    validate_pipeline_changes,
)


class FakeHA:
    def __init__(self):
        self.commands = []
        self.states = {
            "light.luz_do_escritorio": {"entity_id": "light.luz_do_escritorio"},
            "light.sala": {"entity_id": "light.sala"},
        }
        self.exposed = {
            "light.sala": {"conversation": True},
        }
        self.pipeline = {
            "id": "pipeline-1",
            "name": "Home Assistant",
            "language": "pt-BR",
            "conversation_language": "*",
            "conversation_engine": "conversation.home_assistant",
            "stt_engine": None,
            "stt_language": None,
            "tts_engine": None,
            "tts_language": None,
            "tts_voice": None,
            "wake_word_entity": None,
            "wake_word_id": None,
            "prefer_local_intents": True,
        }

    async def get_state(self, entity_id):
        if entity_id not in self.states:
            raise RuntimeError("entity not found")
        return self.states[entity_id]

    async def websocket_command(self, command_type, data=None):
        data = data or {}
        self.commands.append((command_type, data))
        if command_type == "assist_pipeline/pipeline/list":
            return {"pipelines": [dict(self.pipeline)], "preferred_pipeline": "pipeline-1"}
        if command_type == "assist_pipeline/pipeline/get":
            return dict(self.pipeline)
        if command_type == "assist_pipeline/pipeline/update":
            self.pipeline.update({k: v for k, v in data.items() if k != "pipeline_id"})
            return dict(self.pipeline)
        if command_type == "homeassistant/expose_entity/list":
            return {"exposed_entities": dict(self.exposed)}
        if command_type == "homeassistant/expose_new_entities/set":
            return None
        if command_type == "homeassistant/expose_entity":
            for entity_id in data["entity_ids"]:
                settings = self.exposed.setdefault(entity_id, {})
                if data["should_expose"]:
                    settings["conversation"] = True
                else:
                    settings.pop("conversation", None)
                    if not settings:
                        self.exposed.pop(entity_id, None)
            return None
        raise AssertionError(command_type)


def run(coro):
    return asyncio.run(coro)


def test_list_pipelines_uses_official_websocket_command():
    ha = FakeHA()
    result = run(list_pipelines(ha))
    assert result["count"] == 1
    assert result["preferred_pipeline"] == "pipeline-1"
    assert ha.commands[0][0] == "assist_pipeline/pipeline/list"


def test_update_pipeline_merges_and_updates_conversation_engine():
    ha = FakeHA()
    result = run(
        update_pipeline(
            ha,
            "pipeline-1",
            {"conversation_engine": "conversation.codex_assist"},
        )
    )
    assert result["pipeline"]["conversation_engine"] == "conversation.codex_assist"
    command, payload = ha.commands[-1]
    assert command == "assist_pipeline/pipeline/update"
    assert payload["pipeline_id"] == "pipeline-1"
    assert payload["name"] == "Home Assistant"
    assert payload["conversation_engine"] == "conversation.codex_assist"


def test_pipeline_changes_reject_unknown_and_non_conversation_engine():
    with pytest.raises(ValueError):
        validate_pipeline_changes({"arbitrary": "value"})
    with pytest.raises(ValueError):
        validate_pipeline_changes({"conversation_engine": "sensor.bad"})


def test_set_single_entity_exposure_validates_entity_and_calls_official_command():
    ha = FakeHA()
    result = run(set_entity_exposure(ha, "light.luz_do_escritorio", True))
    assert result["exposed"] is True
    command, payload = ha.commands[-1]
    assert command == "homeassistant/expose_entity"
    assert payload == {
        "assistants": ["conversation"],
        "entity_ids": ["light.luz_do_escritorio"],
        "should_expose": True,
    }


def test_exact_exposure_disables_automatic_and_leaves_only_requested_entity():
    ha = FakeHA()
    result = run(set_exact_exposed_entities(ha, ["light.luz_do_escritorio"]))
    assert result["entity_ids"] == ["light.luz_do_escritorio"]
    assert result["enabled"] == ["light.luz_do_escritorio"]
    assert result["disabled"] == ["light.sala"]
    assert (
        "homeassistant/expose_new_entities/set",
        {"assistant": "conversation", "expose_new": False},
    ) in ha.commands


def test_list_exposure_filters_other_assistants():
    ha = FakeHA()
    ha.exposed["light.luz_do_escritorio"] = {"cloud.alexa": True}
    result = run(list_exposed_entities(ha))
    assert result["entity_ids"] == ["light.sala"]


def test_invalid_entity_id_is_rejected_before_websocket_write():
    ha = FakeHA()
    with pytest.raises(ValueError):
        run(set_entity_exposure(ha, "../bad", True))
    assert ha.commands == []
