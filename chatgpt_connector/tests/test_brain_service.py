from brain_service import (
    BrainConfig,
    BrainState,
    compact_state_changed,
    is_priority_event,
)


def _event(entity_id, old, new, *, device_class=None):
    attrs = {"friendly_name": entity_id}
    if device_class:
        attrs["device_class"] = device_class
    return {
        "event_type": "state_changed",
        "time_fired": "2026-09-22T12:00:00+00:00",
        "data": {
            "entity_id": entity_id,
            "old_state": {"state": old, "attributes": attrs},
            "new_state": {"state": new, "attributes": attrs},
        },
    }


def _config(**changes):
    values = {
        "enabled": True,
        "ai_task_entity": "ai_task.codex_assist_ai_task",
        "batch_seconds": 45,
        "min_events_per_batch": 2,
        "max_ai_calls_per_hour": 4,
        "max_ai_calls_per_day": 30,
        "announce_enabled": False,
        "announce_service": "notify.alexa_media_alexa",
        "notify_suggestions": False,
        "tracked_domains": frozenset({"light", "binary_sensor", "vacuum"}),
    }
    values.update(changes)
    return BrainConfig(**values)


def test_compact_filter_keeps_real_light_transition():
    result = compact_state_changed(
        _event("light.escritorio", "off", "on"),
        frozenset({"light"}),
    )
    assert result is not None
    assert result["entity_id"] == "light.escritorio"
    assert result["from"] == "off"
    assert result["to"] == "on"


def test_compact_filter_drops_attribute_only_update():
    result = compact_state_changed(
        _event("light.escritorio", "on", "on"),
        frozenset({"light"}),
    )
    assert result is None


def test_compact_filter_drops_untracked_sensor_noise():
    result = compact_state_changed(
        _event("sensor.cpu_temperature", "41.1", "41.2"),
        frozenset({"light", "binary_sensor"}),
    )
    assert result is None


def test_door_open_is_priority_but_motion_is_not():
    door = compact_state_changed(
        _event("binary_sensor.janela", "off", "on", device_class="window"),
        frozenset({"binary_sensor"}),
    )
    motion = compact_state_changed(
        _event("binary_sensor.movimento", "off", "on", device_class="motion"),
        frozenset({"binary_sensor"}),
    )
    assert door is not None and is_priority_event(door)
    assert motion is not None and not is_priority_event(motion)


def test_hourly_budget_is_hard_limited():
    state = BrainState(_config(max_ai_calls_per_hour=2, max_ai_calls_per_day=10))
    state.data["recent_ai_calls"] = [1000.0, 1100.0]
    assert not state.budget_available(now=1200.0)


def test_daily_budget_is_hard_limited():
    state = BrainState(_config(max_ai_calls_per_hour=10, max_ai_calls_per_day=2))
    state.data["recent_ai_calls"] = [1000.0, 50000.0]
    assert not state.budget_available(now=60000.0)
